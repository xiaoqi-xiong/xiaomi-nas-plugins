import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def run_gateway(sandbox: Path, file_uri: str, *, user: str = "resource-owner", plugin_id: str = ""):
    env = os.environ.copy()
    env.update(
        {
            "FILE_URI": file_uri,
            "REQUEST_METHOD": "GET",
            "CONTENT_LENGTH": "0",
            "QUERY_STRING": "",
            "USER": user,
            "NATIVE_PLUGIN_ID": plugin_id,
            "NATIVE_APP_ROUTE": "",
        }
    )
    return subprocess.run(
        [sys.executable, str(sandbox / "mediastack" / "mediastack.cgi")],
        env=env,
        input=b"",
        capture_output=True,
        check=False,
    )


def setup_sandbox(tmp_path: Path) -> Path:
    """Run the CGI from a temporary app root, never the repository settings file."""
    sandbox = tmp_path / "app"
    shutil.copytree(REPO / "mediastack", sandbox / "mediastack")
    shutil.copy(REPO / "settings.py", sandbox / "settings.py")
    (sandbox / "settings.json").write_text(
        json.dumps(
            {
                "owner": "resource-owner",
                "owner_uid": os.getuid(),
                "nas_host": "127.0.0.1",
                "plugin_namespace": "test-space",
                "ports": {"pt": 1, "public": 1, "pansou": 1, "moviepilot": 1},
            }
        ),
        encoding="utf-8",
    )
    return sandbox


def response(result: subprocess.CompletedProcess):
    head, body = result.stdout.split(b"\r\n\r\n", 1)
    headers = head.decode("latin1").split("\r\n")
    return headers[0], dict(line.split(": ", 1) for line in headers[1:]), body


class ResourceCenterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.sandbox = setup_sandbox(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_center_assets_are_fixed_routes_without_upstream(self):
        for route in ("center", "center/", "center/index.html"):
            html = response(run_gateway(self.sandbox, route))
            self.assertEqual(html[0], "Status: 200 OK")
            self.assertIn("资源中心", html[2].decode("utf-8"))
        native_path = response(
            run_gateway(self.sandbox, "/center/index.html?tab=search", plugin_id="resources")
        )
        self.assertEqual(native_path[0], "Status: 200 OK")
        self.assertIn("资源中心", native_path[2].decode("utf-8"))

        script = response(run_gateway(self.sandbox, "center/resource-center.js"))
        self.assertEqual(script[0], "Status: 200 OK")
        self.assertIn(b"pansou/vendor-ui/index.html", script[2])
        self.assertIn(b"../pt/index.html?embedded=1", script[2])
        self.assertIn(b"__MICRO_APP_PUBLIC_PATH__", script[2])
        self.assertIn(b"contentDocument", script[2])
        self.assertIn(b"mediastack-error", script[2])
        self.assertIn(b"Application temporarily unavailable", script[2])

        style = response(run_gateway(self.sandbox, "center/resource-center.css"))
        self.assertEqual(style[0], "Status: 200 OK")
        self.assertIn(b"bottom-tabs", style[2])
        self.assertIn(b"iframe[hidden]", style[2])
        self.assertIn(b"100dvh", style[2])
        self.assertIn(b".app-shell { width: 100%; height: 100vh; height: 100dvh;", style[2])
        self.assertIn(b"position: absolute", style[2])
        self.assertIn(b".section-description { display: none; }", style[2])
        self.assertIn(b"orientation: landscape", style[2])

    def test_legacy_root_and_alias_routes_are_unchanged(self):
        root = response(run_gateway(self.sandbox, "index.html"))
        self.assertEqual(root[0], "Status: 200 OK")
        self.assertIn(b"mp-frame", root[2])
        self.assertNotIn("资源中心".encode("utf-8"), root[2])

        qb = response(run_gateway(self.sandbox, "pt/index.html"))
        self.assertEqual(qb[0], "Status: 200 OK")
        self.assertIn("登录下载器".encode("utf-8"), qb[2])

        pansou = response(run_gateway(self.sandbox, "pansou/index.html"))
        self.assertEqual(pansou[0], "Status: 200 OK")
        self.assertIn("盘搜".encode("utf-8"), pansou[2])

    def test_resources_plugin_rewrites_upstream_paths_to_its_own_prefix(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", "/signin")
                self.send_header("Set-Cookie", f"QBT_SID_{server.server_port}=sid; Path=/")
                self.end_headers()

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        settings_file = self.sandbox / "settings.json"
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        settings["ports"] = {
            "pt": server.server_port,
            "public": server.server_port,
            "pansou": server.server_port,
            "moviepilot": server.server_port,
        }
        settings_file.write_text(json.dumps(settings), encoding="utf-8")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            env = os.environ.copy()
            env.update(
                {
                    "FILE_URI": "pt/api/v2/app/version",
                    "REQUEST_METHOD": "GET",
                    "CONTENT_LENGTH": "0",
                    "USER": "resource-owner",
                    "NATIVE_PLUGIN_ID": "resources",
                    "NATIVE_APP_ROUTE": "",
                }
            )
            result = subprocess.run(
                [sys.executable, str(self.sandbox / "mediastack" / "mediastack.cgi")],
                env=env,
                input=b"",
                capture_output=True,
                check=False,
            )
            status, headers, _ = response(result)
            self.assertEqual(status, "Status: 302 Found")
            self.assertEqual(headers["Location"], "/plugin/test-space/resources/pt/signin")
        finally:
            server.shutdown()
            server.server_close()

    def test_owner_gate_still_applies_to_center(self):
        denied = response(run_gateway(self.sandbox, "center/index.html", user="someone-else"))
        self.assertEqual(denied[0], "Status: 403 Forbidden")

    def test_upstream_html_errors_get_an_iframe_status_marker(self):
        class ErrorHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"<html><head><title>upstream failure</title></head><body>failed</body></html>"
                self.send_response(500)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), ErrorHandler)
        settings_file = self.sandbox / "settings.json"
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        settings["ports"]["pt"] = server.server_port
        settings_file.write_text(json.dumps(settings), encoding="utf-8")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            env = os.environ.copy()
            env.update(
                {
                    "FILE_URI": "pt/api/v2/app/version",
                    "REQUEST_METHOD": "GET",
                    "CONTENT_LENGTH": "0",
                    "USER": "resource-owner",
                    "NATIVE_PLUGIN_ID": "resources",
                    "NATIVE_APP_ROUTE": "",
                }
            )
            result = subprocess.run(
                [sys.executable, str(self.sandbox / "mediastack" / "mediastack.cgi")],
                env=env,
                input=b"",
                capture_output=True,
                check=False,
            )
            status, _, body = response(result)
            self.assertEqual(status, "Status: 500 Internal Server Error")
            self.assertIn(b'name="mediastack-error" content="500"', body)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
