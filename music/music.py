#!/usr/bin/env python3
"""Owner-scoped, fixed-upstream gateway for the optional Songloft service.

The Xiaomi native host invokes this file as CGI.  It deliberately exposes a
small read/play surface instead of acting as a general HTTP proxy:

* every request is gated by the bundle's owner/UID check;
* the upstream origin comes only from local settings or SONGLOFT_URL;
* only documented Songloft health, auth, song-list, cover, and playback paths
  are routable;
* Xiaomi cookies and native auth headers are never forwarded;
* media responses are copied in chunks so Range playback does not buffer a
  complete file in the CGI process.
"""

import http.client
import json
import mimetypes
import os
import re
import sys
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit

ROOT = Path(__file__).resolve().parent
APP_ROOT = ROOT.parent
sys.path.insert(0, str(APP_ROOT))

from settings import get_settings, owner_is_valid  # noqa: E402


MAX_JSON_BODY = 1024 * 1024
STREAM_CHUNK = 64 * 1024
SONG_ID = re.compile(r"^[1-9][0-9]{0,18}$")
TOKEN_VALUE = re.compile(r"^[A-Za-z0-9._~-]{1,4096}$")
UPSTREAM_PATHS = {
    "/api/v1/health": ("GET", False),
    "/api/v1/version": ("GET", False),
    "/api/v1/songs": ("GET", True),
    "/api/v1/playlists": ("GET", True),
}
SONG_QUERY_KEYS = {"keyword", "type", "path_prefix", "limit", "offset"}
PLAY_QUERY_KEYS = {"v"}
RESPONSE_HEADERS = {
    "accept-ranges",
    "cache-control",
    "content-length",
    "content-range",
    "content-type",
    "etag",
    "last-modified",
}
STATIC_ASSETS = {"music.js", "music.css", "icon.svg", "native-bridge.js"}
ASSET_NAMES = {"index.html"} | STATIC_ASSETS


class GatewayError(Exception):
    """An expected client/configuration/upstream error."""

    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def respond(status, content_type, body, headers=None):
    """Write a complete CGI response."""
    if isinstance(body, str):
        body = body.encode("utf-8")
    extra = headers or {}
    lines = [
        "Status: {}".format(status),
        "Content-Type: {}".format(content_type),
        "Cache-Control: no-store",
        "Content-Length: {}".format(len(body)),
    ]
    lines.extend("{}: {}".format(key, value) for key, value in extra.items())
    sys.stdout.buffer.write(("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + body)


def error_response(status, code, message):
    respond(
        "{} {}".format(status, HTTPStatus(status).phrase),
        "application/json; charset=utf-8",
        json_bytes({"error": code, "message": message}),
    )


def native_path(file_uri):
    """Extract a path relative to ``music/`` from Xiaomi's FILE_URI shape."""
    parsed = urlsplit(file_uri or "")
    path = unquote(parsed.path)
    if path in ("/music", "/music/"):
        return ""
    marker = "/music/"
    if marker in path:
        path = path.split(marker, 1)[1]
    return path.lstrip("/")


def native_cookie_path(file_uri):
    """Return the current plugin directory for a native ``/D:/plugin`` URI."""
    path = unquote(urlsplit(file_uri or "").path)
    marker = "/music"
    index = path.find(marker)
    if index >= 0 and (len(path) == index + len(marker) or path[index + len(marker)] == "/"):
        candidate = path[: index + len(marker)]
        if re.fullmatch(r"/[A-Za-z0-9._:/-]+", candidate):
            return candidate
    return "/music"


def _setting(settings, key, environ):
    music = settings.get("music") if isinstance(settings.get("music"), dict) else {}
    return music.get(key) or settings.get("songloft_{}".format(key)) or environ.get(
        "SONGLOFT_{}".format(key.upper())
    )


def songloft_origin(settings, environ=None):
    """Return the fixed Songloft origin, or ``None`` when unconfigured.

    A configured ``ports.songloft`` value is intentionally loopback-only.  A
    URL may include Songloft's documented BASE_PATH, but may not contain user
    info, a query, or a fragment.
    """
    environ = environ or os.environ
    music = settings.get("music") if isinstance(settings.get("music"), dict) else {}
    candidate = music.get("songloft_url") or settings.get("songloft_url") or environ.get("SONGLOFT_URL")
    if not candidate:
        ports = settings.get("ports") if isinstance(settings.get("ports"), dict) else {}
        port = ports.get("songloft")
        if port:
            try:
                port = int(port)
            except (TypeError, ValueError):
                return None
            if not (1 <= port <= 65535):
                return None
            candidate = "http://127.0.0.1:{}".format(port)
    if not candidate:
        return None
    parsed = urlsplit(str(candidate).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is None or not (1 <= port <= 65535):
        return None
    allowed_hosts = {"127.0.0.1", "localhost", "::1"}
    configured_nas_host = settings.get("nas_host")
    if configured_nas_host:
        allowed_hosts.add(str(configured_nas_host).lower().strip("[]"))
    if parsed.hostname.lower().strip("[]") not in allowed_hosts:
        return None
    return parsed


def upstream_target(origin, endpoint, query=""):
    """Join a fixed origin and a validated endpoint without changing host."""
    prefix = origin.path.rstrip("/")
    path = prefix + endpoint
    return path or "/", query


def _validated_query(raw_query, allowed):
    pairs = parse_qsl(raw_query or "", keep_blank_values=True)
    if any(key not in allowed for key, _ in pairs):
        raise GatewayError(400, "invalid_query", "Unsupported query parameter")
    # Keep the upstream query deterministic and prevent duplicate parameter
    # smuggling.  The UI only submits one value for each key.
    values = {}
    for key, value in pairs:
        if key in values:
            raise GatewayError(400, "invalid_query", "Duplicate query parameter")
        values[key] = value
    return urlencode(values)


def upstream_endpoint(path, raw_query=""):
    """Map a local API path to one of Songloft's documented read/play paths."""
    if path == "api/status":
        return "/api/v1/health", ""
    if path in {"api/health", "api/version"}:
        return "/api/v1/{}".format(path[4:]), ""
    if path in {"api/songs", "api/playlists"}:
        endpoint = "/api/v1/{}".format(path[4:])
        return endpoint, _validated_query(raw_query, SONG_QUERY_KEYS)

    if not path.startswith("api/v1/"):
        raise GatewayError(404, "not_found", "Unknown Music route")
    endpoint = "/" + path
    if endpoint in UPSTREAM_PATHS:
        method, has_query = UPSTREAM_PATHS[endpoint]
        return endpoint, _validated_query(raw_query, SONG_QUERY_KEYS) if has_query else ""

    match = re.fullmatch(r"api/v1/songs/([1-9][0-9]{0,18})/(play(?:\.m3u8)?|cover|lyric|audio-tracks|tracks|artists)", path)
    if match:
        query = _validated_query(raw_query, PLAY_QUERY_KEYS) if match.group(2) in {"play", "play.m3u8", "cover"} else ""
        return endpoint, query
    match = re.fullmatch(r"api/v1/songs/([1-9][0-9]{0,18})", path)
    if match:
        return endpoint, ""
    raise GatewayError(404, "not_found", "Unknown Music route")


def _connection(origin, timeout):
    if origin.scheme == "https":
        return http.client.HTTPSConnection(origin.hostname, origin.port, timeout=timeout)
    return http.client.HTTPConnection(origin.hostname, origin.port, timeout=timeout)


def _timeout(settings, environ):
    value = _setting(settings, "timeout", environ) or 8
    try:
        return max(1, min(60, float(value)))
    except (TypeError, ValueError):
        return 8


def _request(settings, environ, method, endpoint, query="", body=None, auth=None, stream=False):
    origin = songloft_origin(settings, environ)
    if origin is None:
        raise GatewayError(503, "songloft_unconfigured", "Songloft is not configured")
    target_path, target_query = upstream_target(origin, endpoint, query)
    if target_query:
        target_path += "?" + target_query
    headers = {"Accept": "application/json"}
    # Deliberately whitelist only the app's Bearer token and media Range
    # headers.  In particular, do not forward Cookie or Xiaomi auth headers.
    if auth:
        headers["Authorization"] = auth
    if method == "GET" and environ.get("HTTP_RANGE"):
        headers["Range"] = environ["HTTP_RANGE"]
    if method == "GET" and environ.get("HTTP_IF_RANGE"):
        headers["If-Range"] = environ["HTTP_IF_RANGE"]
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(body))
    connection = _connection(origin, _timeout(settings, environ))
    try:
        connection.request(method, target_path, body=body, headers=headers)
        response = connection.getresponse()
    except (OSError, http.client.HTTPException) as exc:
        connection.close()
        raise GatewayError(503, "songloft_unavailable", "Songloft server is unavailable") from exc
    return connection, response


def _auth_header(environ):
    # The browser UI uses a namespaced header.  A generic Authorization header
    # may be supplied by the Xiaomi host itself, so it is intentionally not
    # forwarded to the optional Songloft service.
    value = environ.get("HTTP_X_MUSIC_AUTHORIZATION", "")
    if not value.startswith("Bearer ") or len(value) > 4096:
        value = ""
    if value:
        token = value[7:].strip()
        if TOKEN_VALUE.fullmatch(token):
            return value
    # Audio elements cannot set request headers.  Login responses set this
    # HttpOnly cookie; it is an app-owned token and is the only cookie read.
    for item in environ.get("HTTP_COOKIE", "").split(";"):
        name, _, cookie_value = item.strip().partition("=")
        if name == "songloft_access" and TOKEN_VALUE.fullmatch(cookie_value):
            return "Bearer " + cookie_value
    return None


def _read_body(environ):
    try:
        length = int(environ.get("CONTENT_LENGTH", "0") or "0")
    except ValueError:
        raise GatewayError(400, "invalid_body", "Invalid request body")
    if length < 0 or length > MAX_JSON_BODY:
        raise GatewayError(413, "body_too_large", "Request body is too large")
    return sys.stdin.buffer.read(length)


def _json_request(environ, required):
    body = _read_body(environ)
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise GatewayError(400, "invalid_body", "JSON request body required")
    if not isinstance(data, dict) or not all(isinstance(data.get(key), str) and data[key] for key in required):
        raise GatewayError(400, "invalid_body", "Required credentials are missing")
    if any(key not in required for key in data):
        raise GatewayError(400, "invalid_body", "Unexpected request field")
    return json.dumps({key: data[key] for key in required}, separators=(",", ":")).encode("utf-8")


def _status_payload(settings, environ):
    if songloft_origin(settings, environ) is None:
        return {"configured": False, "online": False, "service": "Songloft"}
    try:
        connection, response = _request(settings, environ, "GET", "/api/v1/health")
    except GatewayError:
        return {"configured": True, "online": False, "service": "Songloft", "reason": "Songloft server is unavailable"}
    try:
        data = response.read(64 * 1024)
    finally:
        connection.close()
    online = 200 <= response.status < 300
    payload = {"configured": True, "online": online, "service": "Songloft"}
    if online:
        try:
            parsed = json.loads(data.decode("utf-8"))
            if isinstance(parsed, dict):
                payload["health"] = parsed
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
    else:
        payload["reason"] = "Songloft server is unavailable"
    return payload


def _forward_response(connection, response, set_auth_cookies=False, clear_auth_cookies=False,
                      cookie_path="/music", secure_cookie=False):
    if set_auth_cookies or clear_auth_cookies:
        _forward_auth_response(
            connection, response, set_auth_cookies, clear_auth_cookies, cookie_path, secure_cookie
        )
        return
    content_type = response.getheader("Content-Type") or "application/octet-stream"
    headers = {}
    for key, value in response.getheaders():
        if key.lower() in RESPONSE_HEADERS and key.lower() != "cache-control":
            headers[key] = value
    status = "{} {}".format(response.status, response.reason or HTTPStatus(response.status).phrase)
    lines = ["Status: {}".format(status), "Content-Type: {}".format(content_type), "Cache-Control: no-store"]
    if "Content-Length" in headers:
        lines.append("Content-Length: {}".format(headers.pop("Content-Length")))
    lines.extend("{}: {}".format(key, value) for key, value in headers.items())
    sys.stdout.buffer.write(("\r\n".join(lines) + "\r\n\r\n").encode("utf-8"))
    try:
        while True:
            chunk = response.read(STREAM_CHUNK)
            if not chunk:
                break
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
    finally:
        connection.close()


def _forward_auth_response(connection, response, set_auth_cookies, clear_auth_cookies, cookie_path, secure_cookie):
    """Forward a small auth response and establish the media-play cookie."""
    try:
        body = response.read(MAX_JSON_BODY)
    finally:
        connection.close()
    cookies = []
    if set_auth_cookies and 200 <= response.status < 300:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        access_token = payload.get("access_token") if isinstance(payload, dict) else None
        refresh_token = payload.get("refresh_token") if isinstance(payload, dict) else None
        if (isinstance(access_token, str) and TOKEN_VALUE.fullmatch(access_token)
                and isinstance(refresh_token, str) and TOKEN_VALUE.fullmatch(refresh_token)):
            max_age = payload.get("expires_in", 604800)
            try:
                max_age = max(60, min(2592000, int(max_age)))
            except (TypeError, ValueError):
                max_age = 604800
            secure = "; Secure" if secure_cookie else ""
            cookie_tail = "; Path={}; HttpOnly; SameSite=Strict{}".format(cookie_path, secure)
            cookies = [
                "songloft_access={}; Max-Age={}{}".format(access_token, max_age, cookie_tail),
                "songloft_refresh={}; Max-Age=2592000{}".format(refresh_token, cookie_tail),
            ]
    elif clear_auth_cookies:
        secure = "; Secure" if secure_cookie else ""
        cookies = [
            "songloft_access=; Max-Age=0; Path={}; HttpOnly; SameSite=Strict{}".format(cookie_path, secure),
            "songloft_refresh=; Max-Age=0; Path={}; HttpOnly; SameSite=Strict{}".format(cookie_path, secure),
        ]
    content_type = response.getheader("Content-Type") or "application/json; charset=utf-8"
    lines = [
        "Status: {} {}".format(response.status, response.reason or HTTPStatus(response.status).phrase),
        "Content-Type: {}".format(content_type),
        "Cache-Control: no-store",
        "Content-Length: {}".format(len(body)),
    ]
    lines.extend("Set-Cookie: {}".format(cookie) for cookie in cookies)
    sys.stdout.buffer.write(("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + body)


def serve():
    settings = get_settings(APP_ROOT)
    environ = os.environ
    if not owner_is_valid(settings, environ.get("USER", ""), os.getuid()):
        error_response(403, "owner_required", "Native owner login required")
        return

    path = native_path(environ.get("FILE_URI", ""))
    cookie_path = native_cookie_path(environ.get("FILE_URI", ""))
    method = environ.get("REQUEST_METHOD", "GET").upper()
    parsed_file_uri = urlsplit(environ.get("FILE_URI", ""))
    query = environ.get("QUERY_STRING", "") or parsed_file_uri.query
    secure_cookie = (environ.get("HTTPS", "").lower() in {"on", "1", "true"}
                     or parsed_file_uri.scheme.lower() == "https")

    try:
        # Some Xiaomi hosts address a CGI entry with ``?asset=...`` (the
        # same shape used by the Resource Monitor entry).  Keep that alias
        # narrow and do not let it turn into a file browser.
        if path == "music.cgi" and query:
            pairs = parse_qsl(query, keep_blank_values=True)
            api_values = [value for key, value in pairs if key == "api"]
            asset_values = [value for key, value in pairs if key == "asset"]
            if asset_values:
                if len(pairs) != 1 or len(asset_values) != 1 or asset_values[0] not in ASSET_NAMES:
                    raise GatewayError(404, "not_found", "Unknown Music asset")
                path, query = asset_values[0], ""
            elif len(api_values) == 1 and api_values[0] and all(key != "api" for key, _ in pairs[1:]):
                api_value = api_values[0].lstrip("/")
                path = "api/" + api_value
                query = urlencode([(key, value) for key, value in pairs if key != "api"])
            else:
                raise GatewayError(404, "not_found", "Unknown Music route")
        elif path == "music.cgi":
            path = "index.html"
        if path in {"", "index.html"}:
            if method != "GET":
                raise GatewayError(405, "method_not_allowed", "Music page requires GET")
            respond("200 OK", "text/html; charset=utf-8", (ROOT / "index.html").read_bytes())
            return
        if path in STATIC_ASSETS:
            if method != "GET":
                raise GatewayError(405, "method_not_allowed", "Static assets require GET")
            if path == "native-bridge.js":
                target = APP_ROOT / "mediastack" / "native-bridge.js"
            elif path == "icon.svg":
                target = APP_ROOT / "assets" / "music.svg"
            else:
                target = ROOT / path
            kind = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            respond("200 OK", kind + ("; charset=utf-8" if kind.startswith("text/") else ""), target.read_bytes())
            return

        if path == "api/status":
            if method != "GET":
                raise GatewayError(405, "method_not_allowed", "Status requires GET")
            respond("200 OK", "application/json; charset=utf-8", json_bytes(_status_payload(settings, environ)))
            return

        if path in {"api/login", "api/refresh", "api/logout"}:
            if method != "POST":
                raise GatewayError(405, "method_not_allowed", "Authentication requires POST")
            if path == "api/login":
                body = _json_request(environ, ["username", "password"])
                endpoint = "/api/v1/auth/login"
                connection, response = _request(settings, environ, "POST", endpoint, body=body)
                _forward_response(connection, response, set_auth_cookies=True, cookie_path=cookie_path,
                                  secure_cookie=secure_cookie)
            elif path == "api/refresh":
                body = _json_request(environ, ["refresh_token"])
                endpoint = "/api/v1/auth/refresh"
                connection, response = _request(settings, environ, "POST", endpoint, body=body)
                _forward_response(connection, response, set_auth_cookies=True, cookie_path=cookie_path,
                                  secure_cookie=secure_cookie)
            else:
                auth = _auth_header(environ)
                if not auth:
                    raise GatewayError(401, "login_required", "Songloft login required")
                connection, response = _request(settings, environ, "POST", "/api/v1/auth/logout", auth=auth)
                _forward_response(connection, response, clear_auth_cookies=True, cookie_path=cookie_path,
                                  secure_cookie=secure_cookie)
            return

        endpoint, validated_query = upstream_endpoint(path, query)
        if method != "GET":
            raise GatewayError(405, "method_not_allowed", "Music API is read-only")
        auth = _auth_header(environ)
        if endpoint not in {"/api/v1/health", "/api/v1/version"} and not auth:
            raise GatewayError(401, "login_required", "Songloft login required")
        connection, response = _request(settings, environ, "GET", endpoint, validated_query, auth=auth)
        _forward_response(connection, response)
    except GatewayError as exc:
        error_response(exc.status, exc.code, exc.message)


if __name__ == "__main__":
    serve()
