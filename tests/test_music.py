import contextlib
import io
import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE = Path(__file__).resolve().parents[1] / "music" / "music.py"
MUSIC_ROOT = MODULE.parent
SPEC = importlib.util.spec_from_file_location("music_gateway", MODULE)
music = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(music)


class FakeStdout:
    def __init__(self):
        self.buffer = io.BytesIO()


class FakeResponse:
    def __init__(self, status=206, body=b"abc", headers=None):
        self.status = status
        self.reason = "Partial Content" if status == 206 else "OK"
        self.body = body
        self.headers = headers or [("Content-Type", "audio/mpeg"), ("Content-Length", str(len(body)))]

    def getheader(self, name, default=None):
        for key, value in self.headers:
            if key.lower() == name.lower():
                return value
        return default

    def getheaders(self):
        return self.headers

    def read(self, size=-1):
        if not self.body:
            return b""
        if size < 0:
            result, self.body = self.body, b""
            return result
        result, self.body = self.body[:size], self.body[size:]
        return result


class FakeConnection:
    def __init__(self, response=None):
        self.response = response or FakeResponse()
        self.closed = False

    def close(self):
        self.closed = True


class MusicGatewayTests(unittest.TestCase):
    def test_music_ui_preserves_hidden_state_and_search_layout(self):
        css = (MUSIC_ROOT / "music.css").read_text(encoding="utf-8")
        html = (MUSIC_ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("[hidden]{display:none!important}", css)
        self.assertIn('<form id="search-form" class="search" role="search">', html)

    def test_music_ui_stops_playback_and_surfaces_failures(self):
        javascript = (MUSIC_ROOT / "music.js").read_text(encoding="utf-8")
        self.assertIn("audio.pause()", javascript)
        self.assertIn('audio.removeAttribute("src")', javascript)
        self.assertIn("audio.replaceChildren()", javascript)
        self.assertIn("player.hidden = true", javascript)
        self.assertIn("播放失败：无法读取音频，请检查登录状态或歌曲文件。", javascript)
        self.assertIn("playback.then(showPlayback).catch(showPlaybackError)", javascript)

    def test_music_ui_rejects_http_status_errors_before_reading_status_payload(self):
        javascript = (MUSIC_ROOT / "music.js").read_text(encoding="utf-8")
        self.assertIn("if (!response.ok)", javascript)
        self.assertIn('setNotice("无法读取 Music 服务状态。", "warn")', javascript)
        self.assertIn('showLogin("网关状态检查失败")', javascript)

    def test_native_file_uri_shape_is_reduced_to_music_asset(self):
        self.assertEqual(
            music.native_path("http://nas.local/cgi-bin/plugin/music/index.html?x=1"),
            "index.html",
        )
        self.assertEqual(music.native_path("/D:/plugin/music/api/songs"), "api/songs")
        self.assertEqual(music.native_path("/D:/plugin/music/music.cgi?asset=music.js"), "music.cgi")
        self.assertEqual(music.native_cookie_path("/D:/plugin/music/music.cgi"), "/D:/plugin/music")
        self.assertEqual(music.native_cookie_path("/music/index.html"), "/music")

    def test_origin_requires_fixed_valid_url_and_supports_loopback_port(self):
        origin = music.songloft_origin({"music": {"songloft_url": "http://127.0.0.1:58091"}}, {})
        self.assertEqual((origin.hostname, origin.port), ("127.0.0.1", 58091))
        self.assertIsNone(music.songloft_origin({"music": {"songloft_url": "http://user:pass@host:1"}}, {}))
        self.assertIsNone(music.songloft_origin({"music": {"songloft_url": "http://host:1/?url=http://evil"}}, {}))
        self.assertIsNone(music.songloft_origin({"music": {"songloft_url": "http://evil.example:58091"}}, {}))
        lan = music.songloft_origin({"nas_host": "nas.local", "music": {"songloft_url": "http://nas.local:58091"}}, {})
        self.assertEqual(lan.hostname, "nas.local")
        fallback = music.songloft_origin({"ports": {"songloft": 58091}}, {})
        self.assertEqual(fallback.hostname, "127.0.0.1")
        self.assertIsNone(music.songloft_origin({"ports": {"songloft": "not-a-port"}}, {}))
        self.assertIsNone(music.songloft_origin({"ports": {"songloft": 0}}, {}))

    def test_arbitrary_host_and_mutation_paths_are_rejected(self):
        with self.assertRaises(music.GatewayError):
            music.upstream_endpoint("api/songs", "url=http%3A%2F%2Fevil.example%2Faudio")
        with self.assertRaises(music.GatewayError):
            music.upstream_endpoint("api/v1/songs/1/delete")
        endpoint, query = music.upstream_endpoint("api/v1/songs/1/play", "v=123")
        self.assertEqual((endpoint, query), ("/api/v1/songs/1/play", "v=123"))

    def test_generic_native_authorization_and_xiaomi_cookie_are_not_forwarded(self):
        environ = {
            "HTTP_AUTHORIZATION": "Bearer xiaomi-native-token",
            "HTTP_COOKIE": "xiaomi_session=secret; other=1",
        }
        self.assertIsNone(music._auth_header(environ))
        self.assertEqual(
            music._auth_header({"HTTP_X_MUSIC_AUTHORIZATION": "Bearer app-token", "HTTP_COOKIE": "xiaomi_session=x"}),
            "Bearer app-token",
        )
        self.assertEqual(music._auth_header({"HTTP_COOKIE": "songloft_access=app-token; xiaomi_session=x"}), "Bearer app-token")

    def test_range_response_streams_with_206_headers(self):
        response = FakeResponse(
            body=b"range-data",
            headers=[
                ("Content-Type", "audio/mpeg"),
                ("Content-Length", "10"),
                ("Content-Range", "bytes 10-19/100"),
                ("Accept-Ranges", "bytes"),
            ],
        )
        connection = FakeConnection(response)
        output = FakeStdout()
        with patch.object(music.sys, "stdout", output):
            music._forward_response(connection, response)
        wire = output.buffer.getvalue()
        self.assertIn(b"Status: 206 Partial Content", wire)
        self.assertIn(b"Content-Range: bytes 10-19/100", wire)
        self.assertIn(b"Accept-Ranges: bytes", wire)
        self.assertTrue(wire.endswith(b"range-data"))
        self.assertTrue(connection.closed)

    def test_unconfigured_status_is_explicit(self):
        self.assertEqual(
            music._status_payload({}, {}),
            {"configured": False, "online": False, "service": "Songloft"},
        )

    def test_unreachable_status_is_offline_without_fake_library(self):
        settings = {"music": {"songloft_url": "http://127.0.0.1:58091"}}
        with patch.object(music, "_request", side_effect=music.GatewayError(503, "songloft_unavailable", "down")):
            status = music._status_payload(settings, {})
        self.assertEqual(status["configured"], True)
        self.assertEqual(status["online"], False)
        self.assertNotIn("songs", status)

    def test_owner_gate_runs_before_static_or_api_work(self):
        stdout = FakeStdout()
        environ = {"FILE_URI": "/music/music.cgi?asset=native-bridge.js", "REQUEST_METHOD": "GET", "USER": "not-owner"}
        with patch.object(music, "get_settings", return_value={"owner": "owner", "owner_uid": 1000}), \
             patch.object(music, "owner_is_valid", return_value=False), \
             patch.object(music.os, "getuid", return_value=1000), \
             patch.dict(os.environ, environ, clear=True), \
             patch.object(music.sys, "stdout", stdout):
            music.serve()
        payload = stdout.buffer.getvalue()
        self.assertIn(b"Status: 403 Forbidden", payload)
        self.assertIn(b'"owner_required"', payload)
        self.assertNotIn(b"hs_callHandler", payload)

    def test_auth_response_sets_only_gateway_owned_cookies(self):
        response = FakeResponse(status=200, body=json.dumps({
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_in": 600,
        }).encode(), headers=[("Content-Type", "application/json")])
        output = FakeStdout()
        with patch.object(music.sys, "stdout", output):
            music._forward_response(FakeConnection(response), response, set_auth_cookies=True)
        wire = output.buffer.getvalue()
        self.assertIn(b"Set-Cookie: songloft_access=access", wire)
        self.assertIn(b"Path=/music", wire)
        self.assertIn(b"Set-Cookie: songloft_refresh=refresh", wire)
        self.assertNotIn(b"xiaomi", wire.lower())

    def test_invalid_upstream_tokens_are_not_set_as_cookies(self):
        response = FakeResponse(status=200, body=json.dumps({
            "access_token": "access;bad",
            "refresh_token": "refresh",
            "expires_in": 600,
        }).encode(), headers=[("Content-Type", "application/json")])
        output = FakeStdout()
        with patch.object(music.sys, "stdout", output):
            music._forward_response(FakeConnection(response), response, set_auth_cookies=True)
        self.assertNotIn(b"Set-Cookie", output.buffer.getvalue())

    def test_https_auth_cookie_is_marked_secure(self):
        response = FakeResponse(status=200, body=json.dumps({
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_in": 600,
        }).encode(), headers=[("Content-Type", "application/json")])
        output = FakeStdout()
        with patch.object(music.sys, "stdout", output):
            music._forward_response(FakeConnection(response), response, set_auth_cookies=True, secure_cookie=True)
        self.assertIn(b"SameSite=Strict; Secure", output.buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
