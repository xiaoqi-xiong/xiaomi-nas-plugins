import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
COPY_DIRS = ("mediastack", "mteam", "xproxy", "resource-monitor", "music", "assets")
CGI_NAMES = {"mteam": "mteam.cgi", "xproxy": "xproxy.cgi", "resource-monitor": "monitor.cgi"}


def make_sandbox(root: Path) -> Path:
    sandbox = root / "app"
    for name in COPY_DIRS:
        shutil.copytree(
            REPO / name,
            sandbox / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    shutil.copy(REPO / "settings.py", sandbox / "settings.py")
    (sandbox / "settings.json").write_text(
        json.dumps(
            {
                "owner": "test-owner",
                "owner_uid": os.getuid(),
                "nas_host": "127.0.0.1",
                "proxy_controller_host": "127.0.0.1",
                "plugin_namespace": "test-space",
                "ports": {
                    "moviepilot": 1,
                    "pt": 1,
                    "public": 1,
                    "pansou": 1,
                    "proxy_controller": 1,
                    "songloft": 1,
                },
            }
        ),
        encoding="utf-8",
    )
    return sandbox


def run_cgi(sandbox: Path, plugin: str, file_uri: str, *, user: str = "test-owner"):
    environment = os.environ.copy()
    environment.update(
        {
            "FILE_URI": file_uri,
            "REQUEST_METHOD": "GET",
            "CONTENT_LENGTH": "0",
            "QUERY_STRING": "",
            "USER": user,
        }
    )
    return subprocess.run(
        [sys.executable, str(sandbox / plugin / CGI_NAMES[plugin])],
        env=environment,
        input=b"",
        capture_output=True,
        timeout=5,
        check=False,
    )


def parse_response(result: subprocess.CompletedProcess):
    head, body = result.stdout.split(b"\r\n\r\n", 1)
    lines = head.decode("latin1").split("\r\n")
    headers = dict(line.split(": ", 1) for line in lines[1:])
    return lines[0], headers, body


class NativeNavigationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.sandbox = make_sandbox(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_mteam_and_xproxy_native_bridge_routes_serve_canonical_bridge(self):
        canonical = (self.sandbox / "mediastack/native-bridge.js").read_bytes()
        for plugin in ("mteam", "xproxy"):
            with self.subTest(plugin=plugin):
                result = run_cgi(self.sandbox, plugin, "native-bridge.js")
                status, headers, body = parse_response(result)
                self.assertEqual(status, "Status: 200 OK")
                self.assertEqual(headers["Content-Type"], "application/javascript")
                self.assertEqual(body, canonical)

    def test_mteam_and_xproxy_reject_invalid_owner_before_static_route(self):
        for plugin in ("mteam", "xproxy"):
            with self.subTest(plugin=plugin):
                result = run_cgi(self.sandbox, plugin, "native-bridge.js", user="other-user")
                status, _, body = parse_response(result)
                self.assertEqual(status, "Status: 403 Forbidden")
                self.assertIn(b"Native owner login required", body)

    def test_resource_monitor_svg_allowlist_serves_real_svg_assets(self):
        for asset in ("icon.svg", "docker-icon.svg"):
            with self.subTest(asset=asset):
                result = run_cgi(self.sandbox, "resource-monitor", asset)
                status, headers, body = parse_response(result)
                self.assertEqual(status, "Status: 200 OK")
                self.assertEqual(headers["Content-Type"], "image/svg+xml; charset=utf-8")
                root = ET.fromstring(body)
                self.assertEqual(root.tag.rsplit("}", 1)[-1], "svg")

    def test_resource_monitor_static_assets_keep_owner_gate_and_allowlist(self):
        denied = run_cgi(self.sandbox, "resource-monitor", "icon.svg", user="other-user")
        status, _, body = parse_response(denied)
        self.assertEqual(status, "Status: 403 Forbidden")
        self.assertIn(b"Plugin owner login required", body)

        unknown = run_cgi(self.sandbox, "resource-monitor", "unknown.svg")
        status, _, body = parse_response(unknown)
        self.assertEqual(status, "Status: 404 Not Found")
        self.assertIn(b"Unknown resource monitor route", body)


if __name__ == "__main__":
    unittest.main()
