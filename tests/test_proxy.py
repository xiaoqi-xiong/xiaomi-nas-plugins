import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest


REPO = Path(__file__).resolve().parents[1]


class ProxyStatusTests(unittest.TestCase):
    def test_idle_controller_null_connections_remains_available(self):
        class Controller(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                values = {
                    '/version': {'version': 'test'},
                    '/configs': {'mode': 'rule', 'tun': None},
                    '/connections': {'connections': None, 'downloadTotal': 0, 'uploadTotal': 0},
                    '/proxies': {'proxies': {'Proxy': {'type': 'Selector', 'now': 'DIRECT', 'all': ['DIRECT']}}},
                }
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(values[self.path]).encode())

            def log_message(self, *_args):
                pass

        server = http.server.HTTPServer(('127.0.0.1', 0), Controller)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                shutil.copytree(REPO / 'xproxy', root / 'xproxy')
                shutil.copy(REPO / 'settings.py', root / 'settings.py')
                (root / 'private').mkdir(mode=0o700)
                (root / 'private/controller.json').write_text('{"secret":"private-test-value"}')
                (root / 'settings.json').write_text(json.dumps({
                    'owner': 'test-owner', 'owner_uid': os.getuid(),
                    'ports': {'proxy_controller': server.server_port},
                }))
                env = {**os.environ, 'USER': 'test-owner', 'FILE_URI': 'native/status', 'REQUEST_METHOD': 'GET'}
                result = subprocess.run([sys.executable, str(root / 'xproxy/xproxy.cgi')], env=env,
                                        capture_output=True, check=True)
                head, body = result.stdout.split(b'\r\n\r\n', 1)
                self.assertTrue(head.startswith(b'Status: 200 OK'))
                data = json.loads(body)
                self.assertEqual(data['connections'], 0)
                self.assertEqual(data['download_total'], 0)
                self.assertFalse(data['tun'])
                self.assertEqual(len(data['groups']), 1)
                self.assertNotIn(b'private-test-value', result.stdout)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)
