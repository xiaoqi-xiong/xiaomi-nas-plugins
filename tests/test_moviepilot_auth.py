import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from test_resource_center import setup_sandbox, response


class MoviePilotAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.sandbox = setup_sandbox(Path(self.temp.name))
        self.calls = []
        self.valid = True
        test = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                test.calls.append((self.path, parse_qs(body.decode()), dict(self.headers)))
                result = {'access_token': 'app-token', 'token_type': 'bearer',
                          'super_user': True, 'user_id': 1, 'user_name': 'test-admin',
                          'avatar': '', 'level': 1, 'permissions': {},
                          'unexpected_private_field': 'must-not-leave-server'} if test.valid else {'detail': 'MFA required'}
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())

            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.end_headers()
                self.wfile.write(b'<html><head><script type="module" crossorigin src="./assets/main.js"></script></head></html>')

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        config = self.sandbox / 'settings.json'
        data = json.loads(config.read_text())
        data['ports']['moviepilot'] = self.server.server_port
        config.write_text(json.dumps(data))
        private = self.sandbox / 'private'
        private.mkdir(mode=0o700)
        self.saved = private / 'native-login.json'
        self.creds = {'username': 'test-admin', 'password': 'fake-password'}
        self.saved.write_text(json.dumps({'moviepilot': self.creds, 'pt': {'keep': True}}))
        self.saved.chmod(0o600)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def request(self, method='GET', body=None, user='resource-owner', path='moviepilot/native/session'):
        raw = json.dumps(body).encode() if body is not None else b''
        env = {**os.environ, 'FILE_URI': path, 'USER': user, 'REQUEST_METHOD': method,
               'CONTENT_LENGTH': str(len(raw)), 'QUERY_STRING': '', 'NATIVE_APP_ROUTE': '',
               'HTTP_AUTHORIZATION': 'native-secret', 'HTTP_COOKIE': 'native=private',
               'NATIVE_PLUGIN_ID': ''}
        result = subprocess.run([sys.executable, str(self.sandbox / 'mediastack/mediastack.cgi')],
                                env=env, input=raw, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return response(result)

    def test_fresh_owner_session_returns_only_official_store_fields(self):
        status, headers, raw = self.request()
        self.assertEqual(status, 'Status: 200 OK')
        data = json.loads(raw)
        self.assertEqual(data['auth'], {'token': 'app-token', 'remember': True, 'originalPath': None})
        self.assertTrue(data['user']['superUser'])
        self.assertNotIn(b'fake-password', raw)
        self.assertNotIn(b'must-not-leave-server', raw)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        path, body, upstream = self.calls[0]
        self.assertEqual(path, '/api/v1/login/access-token')
        self.assertEqual(body, {k: [v] for k, v in self.creds.items()})
        self.assertNotIn('Authorization', upstream)
        self.assertNotIn('Cookie', upstream)

    def test_owner_gate_precedes_credential_and_upstream_access(self):
        self.assertEqual(self.request(user='another-user')[0], 'Status: 403 Forbidden')
        self.assertEqual(self.calls, [])

    def test_invalid_or_mfa_login_does_not_claim_success_or_change_saved_credentials(self):
        before = self.saved.read_bytes()
        self.valid = False
        self.assertEqual(self.request()[0], 'Status: 401 Unauthorized')
        self.assertEqual(self.saved.read_bytes(), before)

    def test_logout_disables_reopening_without_affecting_other_apps(self):
        self.assertEqual(self.request('DELETE')[0], 'Status: 200 OK')
        self.assertEqual(json.loads(self.saved.read_text()), {'pt': {'keep': True}})
        self.assertEqual(self.saved.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.request()[0], 'Status: 401 Unauthorized')
        self.assertEqual(self.calls, [])

    def test_explicit_remember_is_required_for_manual_login(self):
        for remember in [False, True]:
            with self.subTest(remember=remember):
                self.assertEqual(self.request('POST', {**self.creds, 'remember': remember})[0], 'Status: 200 OK')
                self.assertEqual('moviepilot' in json.loads(self.saved.read_text()), remember)

    def test_official_module_waits_for_native_session(self):
        status, _, raw = self.request(path='moviepilot/index.html')
        self.assertEqual(status, 'Status: 200 OK')
        html = raw.decode()
        self.assertIn('<script src="./bridge-auth.js"></script>', html)
        self.assertIn('await window.nativeMoviePilotReady; import("./assets/main.js")', html)
        self.assertNotIn('crossorigin src=', html)


if __name__ == '__main__':
    unittest.main()
