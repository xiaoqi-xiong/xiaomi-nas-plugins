import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_music import music, FakeConnection, FakeResponse, FakeStdout


class MusicSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.private = self.root / 'private'
        self.private.mkdir(mode=0o700)
        self.saved = self.private / 'native-login.json'
        self.credentials = {'username': 'admin', 'password': 'fake-music-password'}
        self.saved.write_text(json.dumps({'music': self.credentials, 'pt': {'keep': True}}))
        self.settings = {'owner': 'test-owner', 'owner_uid': os.getuid(), 'ports': {'songloft': 58091}}
        self.app_patch = patch.object(music, 'APP_ROOT', self.root)
        self.app_patch.start()
        self.tokens = {'access_token': 'app-access', 'refresh_token': 'app-refresh', 'expires_in': 600, 'token_type': 'Bearer'}

    def tearDown(self):
        self.app_patch.stop()
        self.temp.cleanup()

    def auth_response(self, status=200, payload=None):
        response = FakeResponse(status=status, body=json.dumps(payload or self.tokens).encode())
        return FakeConnection(response), response

    def test_reopen_reuses_owner_only_app_cache_without_repeated_password_logins(self):
        with patch.object(music, '_request', return_value=self.auth_response()) as request:
            self.assertEqual(music.native_session(self.settings, {}), self.tokens)
            self.assertEqual(music.native_session(self.settings, {}), self.tokens)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[3], '/api/v1/auth/login')
        self.assertEqual(json.loads(request.call_args.kwargs['body']), self.credentials)
        cache = self.private / 'music-session.json'
        self.assertEqual(cache.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('fake-music-password', cache.read_text())

    def test_expiry_refreshes_and_invalid_refresh_falls_back_once_to_saved_login(self):
        with patch.object(music, '_request', return_value=self.auth_response()):
            music.native_session(self.settings, {})
        cache = self.private / 'music-session.json'
        data = json.loads(cache.read_text()); data['expires_at'] = 0
        cache.write_text(json.dumps(data))
        with patch.object(music, '_request', side_effect=[self.auth_response(401), self.auth_response()]) as request:
            music.native_session(self.settings, {})
        self.assertEqual([call.args[3] for call in request.call_args_list], ['/api/v1/auth/refresh', '/api/v1/auth/login'])

    def test_bad_login_does_not_replace_other_or_existing_credentials(self):
        before = self.saved.read_bytes()
        with patch.object(music, '_request', return_value=self.auth_response(401)):
            with self.assertRaises(music.GatewayError):
                music.native_session(self.settings, {}, credentials={'username': 'wrong', 'password': 'bad'})
        self.assertEqual(self.saved.read_bytes(), before)
        self.assertFalse((self.private / 'music-session.json').exists())

    def test_logout_removes_only_music_and_disables_next_reopen(self):
        with patch.object(music, '_request', return_value=self.auth_response()):
            music.native_session(self.settings, {})
        music.native_session(self.settings, {}, forget=True)
        self.assertEqual(json.loads(self.saved.read_text()), {'pt': {'keep': True}})
        self.assertEqual(self.saved.stat().st_mode & 0o777, 0o600)
        self.assertIsNone(music.native_session(self.settings, {}))
        self.assertFalse((self.private / 'music-session.json').exists())

    def run_cgi(self, path, method='GET'):
        output = FakeStdout()
        env = {'USER': 'test-owner', 'FILE_URI': path, 'REQUEST_METHOD': method, 'CONTENT_LENGTH': '0',
               'HTTP_AUTHORIZATION': 'Bearer native-secret', 'HTTP_COOKIE': 'xiaomi_session=native-cookie'}
        with patch.object(music, 'get_settings', return_value=self.settings), \
                patch.dict(os.environ, env, clear=True), patch.object(music.sys, 'stdout', output):
            music.serve()
        return output.buffer.getvalue()

    def test_headerless_audio_uses_owner_saved_session_and_streams_real_range(self):
        media = FakeResponse(body=b'range-data', headers=[('Content-Type', 'audio/flac'), ('Content-Range', 'bytes 0-9/100')])
        with patch.object(music, '_request', side_effect=[self.auth_response(), (FakeConnection(media), media)]) as request:
            wire = self.run_cgi('api/v1/songs/1/play')
        self.assertIn(b'Status: 206 Partial Content', wire)
        self.assertTrue(wire.endswith(b'range-data'))
        self.assertEqual(request.call_args.kwargs['auth'], 'Bearer app-access')

    def test_scan_uses_fixed_safe_body_and_does_not_open_arbitrary_mutation_proxy(self):
        result = FakeResponse(status=200, body=b'{"message":"started"}')
        with patch.object(music, '_request', side_effect=[self.auth_response(), (FakeConnection(result), result)]) as request:
            wire = self.run_cgi('api/scan', 'POST')
        self.assertIn(b'Status: 200 OK', wire)
        self.assertEqual(request.call_args.args[3], '/api/v1/scan')
        self.assertEqual(request.call_args.kwargs['body'], b'{"reimport":false}')
        self.assertIn(b'Status: 405', self.run_cgi('api/scan', 'DELETE'))

    def test_official_frontend_waits_for_native_login_and_uses_local_flutter_assets(self):
        html = b'<html><head><base href="/"></head><body><script>_flutter.loader.load({ config: config });</script></body></html>'
        result = FakeResponse(status=200, body=html, headers=[('Content-Type', 'text/html')])
        with patch.object(music, '_request', return_value=(FakeConnection(result), result)):
            wire = self.run_cgi('vendor-ui/index.html')
        self.assertIn(b'<base href="./">', wire)
        self.assertIn(b'../songloft-ui-bridge.js', wire)
        self.assertIn(b'window.nativeSongloftReady.then', wire)
        self.assertIn(b'canvasKitBaseUrl = "canvaskit/"', wire)

    def test_official_media_drops_url_tokens_and_retains_owner_application_session(self):
        media = FakeResponse(body=b'range-data')
        with patch.object(music, '_request', side_effect=[self.auth_response(), (FakeConnection(media), media)]) as request:
            wire = self.run_cgi('vendor-ui/api/v1/songs/1/play?access_token=not-native&v=1')
        self.assertTrue(wire.endswith(b'range-data'))
        self.assertEqual(request.call_args.args[3:5], ('/api/v1/songs/1/play', 'v=1'))
        self.assertEqual(request.call_args.kwargs['auth'], 'Bearer app-access')

    def test_official_assets_reject_directory_traversal_before_upstream(self):
        with patch.object(music, '_request') as request:
            wire = self.run_cgi('vendor-ui/assets/%2e%2e/private/config')
        self.assertIn(b'Status: 404', wire)
        request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
