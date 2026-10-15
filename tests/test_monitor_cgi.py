import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def _response(result: subprocess.CompletedProcess):
    head, body = result.stdout.split(b"\r\n\r\n", 1)
    lines = head.decode("latin1").split("\r\n")
    return lines[0], dict(line.split(": ", 1) for line in lines[1:]), body


class ResourceMonitorCgiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name) / "app"
        shutil.copytree(REPO / "resource-monitor", root / "resource-monitor")
        shutil.copytree(REPO / "mediastack", root / "mediastack")
        shutil.copy(REPO / "settings.py", root / "settings.py")
        (root / "settings.json").write_text(json.dumps({
            "owner": "resource-owner", "owner_uid": os.getuid(),
            "nas_host": "127.0.0.1", "plugin_namespace": "test-space",
        }), encoding="utf-8")
        self.root = root
        self.cgi = root / "resource-monitor" / "monitor.cgi"

    def tearDown(self):
        self.temp.cleanup()

    def run_cgi(self, file_uri, *, user="resource-owner", method="GET", query=""):
        env = os.environ.copy()
        env.update({"FILE_URI": file_uri, "QUERY_STRING": query,
                    "REQUEST_METHOD": method, "USER": user,
                    "CONTENT_LENGTH": "0"})
        return subprocess.run([sys.executable, str(self.cgi)], env=env,
                              input=b"", capture_output=True, check=False)

    def test_owner_gate_applies_to_docker_alias(self):
        denied = _response(self.run_cgi("docker-monitor/index.html", user="someone-else"))
        self.assertEqual(denied[0], "Status: 403 Forbidden")
        self.assertEqual(json.loads(denied[2])["error"], "Plugin owner login required")

        allowed = _response(self.run_cgi("docker-monitor/index.html"))
        self.assertEqual(allowed[0], "Status: 200 OK")
        self.assertIn("系统监控", allowed[2].decode("utf-8"))

    def test_alias_api_and_whitelisted_assets(self):
        api = _response(self.run_cgi("docker-monitor/monitor.cgi", query="api=docker"))
        self.assertEqual(api[0], "Status: 200 OK")
        self.assertIn("available", json.loads(api[2]))

        script = _response(self.run_cgi("docker-monitor/monitor.cgi", query="asset=monitor.js"))
        self.assertEqual(script[0], "Status: 200 OK")
        self.assertEqual(script[1]["Content-Type"], "application/javascript; charset=utf-8")
        self.assertIn(b"function", script[2])

        style = _response(self.run_cgi("resource-monitor/style.css"))
        self.assertEqual(style[0], "Status: 200 OK")
        self.assertEqual(style[1]["Content-Type"], "text/css; charset=utf-8")

        bridge = _response(self.run_cgi("docker-monitor/monitor.cgi", query="asset=native-bridge.js"))
        self.assertEqual(bridge[0], "Status: 200 OK")
        self.assertEqual(bridge[1]["Content-Type"], "application/javascript; charset=utf-8")

    def test_unknown_route_and_non_get_are_rejected(self):
        unknown = _response(self.run_cgi("docker-monitor/private.txt"))
        self.assertEqual(unknown[0], "Status: 404 Not Found")
        post = _response(self.run_cgi("docker-monitor/index.html", method="POST"))
        self.assertEqual(post[0], "Status: 405 Method Not Allowed")


if __name__ == "__main__":
    unittest.main()
