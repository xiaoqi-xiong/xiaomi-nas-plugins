#!/usr/bin/env python3
"""Local-only QA fixture server for the bundled native plugin UIs.

This server is deliberately separate from the production CGI handlers.  It
serves the checked-in HTML/CSS/JS and a small in-memory fixture for browser QA;
it never reads settings, credentials, NAS state, or Docker state, and it never
writes a fixture file.  Bind is fixed to 127.0.0.1 so an accidental launch
cannot expose the fixtures to the LAN.

Examples after starting the server (the mode is also shown by the in-page
fixture banner):

  /resource-monitor/index.html                 host + Docker sample data
  /resource-monitor/index.html?mode=empty     empty host/container data
  /resource-monitor/index.html?mode=error     host/Docker HTTP errors
  /resource-monitor/index.html#docker         open the Docker tab
  /center/index.html?mode=ok                  resource-center mock iframes
  /center/index.html?mode=error               resource-center failed iframes
  /music/index.html                           mock Songloft login and songs
  /music/index.html?mode=empty                empty mock Songloft library
  /music/index.html?mode=error                unavailable mock Songloft

The process prints the exact URLs when it starts.  Stop it with Ctrl-C.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import time
import wave
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
HOST = "127.0.0.1"
MODES = {"ok", "empty", "error"}
PREVIEW_HEADER = "X-Preview-Fixture"


def _wav_fixture() -> bytes:
    """Generate a deterministic short tone in memory for Range playback QA."""
    rate = 8000
    duration = 0.6
    frames = int(rate * duration)
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        data = bytearray()
        for index in range(frames):
            sample = int(9000 * math.sin(2 * math.pi * 440 * index / rate))
            data.extend(sample.to_bytes(2, "little", signed=True))
        stream.writeframes(bytes(data))
    return output.getvalue()


AUDIO_FIXTURE = _wav_fixture()


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _mode(value: str | None) -> str:
    return value if value in MODES else "ok"


def _safe_file(relative: str) -> Path | None:
    """Resolve a known public fixture asset without making a file browser."""
    if not relative or "\\" in relative or any(part in {"", ".", ".."} for part in relative.split("/")):
        return None
    target = (ROOT / relative).resolve()
    try:
        target.relative_to(ROOT.resolve())
    except ValueError:
        return None
    return target if target.is_file() else None


def _fixture_banner(kind: str, mode: str) -> bytes:
    label = f"QA FIXTURE · {kind} · {mode} · 模拟数据"
    escaped = (label.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
               .replace('"', "&quot;"))
    return ("<style id=\"qa-fixture-style\">"
            ".qa-fixture{position:fixed;left:10px;bottom:10px;z-index:2147483647;"
            "padding:7px 10px;border:1px solid #f4c95d;border-radius:9px;"
            "background:#fff8d9;color:#624b16;font:600 12px system-ui;"
            "box-shadow:0 2px 12px #0002;pointer-events:none}"
            "</style><div class=\"qa-fixture\" role=\"status\">" + escaped +
            "</div>").encode("utf-8")


def _native_mock_fixture() -> bytes:
    """Return an opt-in in-page simulator for the native back bridge."""
    return ("<style id=\"qa-native-mock-style\">"
            ".qa-native-return{position:fixed;right:10px;top:10px;z-index:2147483647;"
            "padding:6px 9px;border:1px solid #93c5fd;border-radius:8px;"
            "background:#eff6ff;color:#1e3a8a;font:600 11px system-ui;"
            "box-shadow:0 2px 10px #0002;pointer-events:none}"
            "</style><script id=\"qa-native-mock\">(function(){"
            "var count=0,marker=document.createElement('div');"
            "marker.className='qa-native-return';marker.id='qa-native-return-count';"
            "marker.setAttribute('role','status');"
            "function render(){marker.textContent='QA native-return count: '+count+' · normal_goback → code 0';}"
            "function install(){document.body.appendChild(marker);render();"
            "var bridge=window.flutter_inappwebview||{};"
            "bridge.callHandler=function(handler,name,payload,id){"
            "if(handler==='hs_webCallAppHandler'&&name==='normal_goback'){"
            "window.setTimeout(function(){count++;render();"
            "if(typeof window.hs_appCallBackToWeb==='function')"
            "window.hs_appCallBackToWeb(id, JSON.stringify({code:0}));},0);}"
            "return Promise.resolve(0);};window.flutter_inappwebview=bridge;}"
            "if(document.body)install();else document.addEventListener('DOMContentLoaded',install,{once:true});"
            "})();</script>").encode("utf-8")


def _with_banner(body: bytes, kind: str, mode: str, native_mock: bool = False) -> bytes:
    """Keep the real UI bytes while adding an in-memory, visible QA marker."""
    marker = _fixture_banner(kind, mode)
    if native_mock:
        marker += _native_mock_fixture()
    lower = body.lower()
    position = lower.rfind(b"</body>")
    if position < 0:
        return marker + body
    return body[:position] + marker + body[position:]


def _sample_host(mode: str) -> dict:
    now = int(time.time())
    if mode == "empty":
        return {
            "sampled_at": now, "sample_state": "unavailable",
            "warnings": ["QA fixture：主机无数据"], "uptime_seconds": None,
            "cpu_percent": None, "cpu_cores": None, "load_average": [],
            "system": {"model": "Xiaomi NAS QA Fixture", "cpu_model": "ARM Cortex-A55 (模拟)",
                       "architecture": "aarch64", "kernel": "6.1.0-qa", "os_name": "Xiaomi OS QA",
                       "os_version": "qa-1", "firmware_version": "QA-FW-1.0"},
            "memory": {key: None for key in ("total", "available", "used", "percent",
                                               "free", "buffers", "cached", "swap_total", "swap_used")},
            "network": [], "disk_io": {}, "volumes": [], "temperatures": [],
        }
    return {
        "sampled_at": now, "sample_state": "ok", "warnings": ["QA fixture：模拟主机数据"],
        "uptime_seconds": 86400 + 3720, "cpu_percent": 23.4, "cpu_cores": 8,
        "load_average": ["0.84", "0.72", "0.61"],
        "system": {"model": "Xiaomi NAS QA Fixture", "cpu_model": "ARM Cortex-A55 (模拟)",
                   "architecture": "aarch64", "kernel": "6.1.0-qa", "os_name": "Xiaomi OS QA",
                   "os_version": "qa-1", "firmware_version": "QA-FW-1.0"},
        "memory": {"total": 16 * 1000**3, "available": 9.2 * 1000**3,
                    "used": 6.8 * 1000**3, "percent": 42.5,
                    "free": 4.2 * 1000**3, "buffers": 512 * 1000**2,
                    "cached": 4.5 * 1000**3, "swap_total": 2 * 1000**3,
                    "swap_used": 128 * 1000**2},
        "network": [{"name": "bond0", "rx": 987654321, "tx": 345678901,
                      "rx_rate": 1843200, "tx_rate": 532480}],
        "disk_io": {"sda": {"read_rate": 2457600, "write_rate": 860160}},
        "volumes": [{"source": "/dev/sda1", "mount": "/share", "fs": "ext4",
                      "total": 4 * 1000**4, "free": 1.7 * 1000**4,
                      "used_percent": 57.5}],
        "temperatures": [{"name": "cpu_thermal", "celsius": 48.5}],
    }


def _sample_docker(mode: str) -> dict:
    now = int(time.time())
    if mode == "empty":
        return {"available": True, "sampled_at": now, "sample_state": "ok",
                "running": 0, "stopped": 0, "total": 0, "projects": [],
                "project_count": 0, "stats_available": False,
                "warnings": ["QA fixture：没有容器数据"], "containers": []}
    return {"available": True, "sampled_at": now, "sample_state": "ok",
            "running": 2, "stopped": 1, "total": 3,
            "projects": [{"name": "media", "total": 2, "running": 2, "stopped": 0},
                         {"name": "", "total": 1, "running": 0, "stopped": 1}],
            "project_count": 2, "stats_available": True,
            "warnings": ["QA fixture：模拟 Docker 状态"],
            "containers": [
                {"id": "a1b2c3d4e5f6", "name": "moviepilot", "image": "mp:preview",
                 "state": "running", "status": "Up 2 hours", "ports": "0.0.0.0:3000->3000/tcp",
                 "project": "media", "stats": {"cpu": "4.2%", "memory": "420MiB / 2GiB",
                 "memory_percent": "20.5%", "network": "1.2MB / 840kB", "block_io": "12MB / 4MB"}},
                {"id": "b2c3d4e5f6a7", "name": "jellyfin", "image": "jellyfin:preview",
                 "state": "running", "status": "Up 40 minutes", "ports": "0.0.0.0:8096->8096/tcp",
                 "project": "media", "stats": {"cpu": "8.7%", "memory": "1.1GiB / 4GiB",
                 "memory_percent": "27.5%", "network": "8.4MB / 2.1MB", "block_io": "90MB / 21MB"}},
                {"id": "c3d4e5f6a7b8", "name": "old-worker", "image": "worker:preview",
                 "state": "exited", "status": "Exited (0) 3 days ago", "ports": "",
                 "project": "", "stats": {"cpu": "—", "memory": "—", "memory_percent": "—",
                 "network": "—", "block_io": "—"}},
            ]}


SONGS = [
    {"id": 1, "title": "Midnight Preview", "artist": "QA Ensemble", "album": "Fixture Sessions",
     "cover_url": "/api/v1/songs/1/cover"},
    {"id": 2, "title": "Loopback Sunrise", "artist": "Local Signals", "album": "Fixture Sessions",
     "cover_url": "/api/v1/songs/2/cover"},
    {"id": 3, "title": "No Credentials Required", "artist": "Test Harness", "album": "QA Only",
     "cover_url": "/api/v1/songs/3/cover"},
]


# These rows deliberately resemble qBittorrent's Web API payloads rather than
# the production plugin's internal objects.  Keeping the fixture data here
# makes the preview server useful for the real qB UI without ever contacting a
# downloader or reading a credential.
QB_TASKS = {
    "pt": [
        {
            "hash": "a" * 40,
            "name": "庆余年 第二季 / Qing.Yu.Nian.2024.S02.2160p.WEB-DL.H265.DV.HDR.DDP5.1.Atmos-PTQA",
            "state": "stalledUP",
            "progress": 1.0,
            "content_path": "/media/pt/庆余年 第二季 / Qing.Yu.Nian.2024.S02.2160p.WEB-DL.H265.DV.HDR.DDP5.1.Atmos-PTQA/庆余年.S02E36.2160p.mkv",
            "total_size": 18_432_000_000,
            "size": 18_432_000_000,
            "uploaded": 42_786_123_456,
            "ratio": 2.32,
            "upspeed": 786_432,
            "dlspeed": 0,
            "num_leechs": 3,
            "num_incomplete": 8,
            "added_on": 1_757_550_000,
            "save_path": "/media/pt/",
            "eta": 0,
            "category": "电视剧",
            "tags": "keep-seeding,中文",
            "num_seeds": 14,
            "amount_left": 0,
        },
        {
            "hash": "b" * 40,
            "name": "The.Last.of.Us.S02.2025.2160p.MAX.WEB-DL.DV.HDR10Plus.HEVC.DDP5.1-ChineseSubtitleLongFixtureName",
            "state": "downloading",
            "progress": 0.643,
            "content_path": "/media/pt/The.Last.of.Us.S02.2025.2160p.MAX.WEB-DL.DV.HDR10Plus.HEVC.DDP5.1-ChineseSubtitleLongFixtureName/The.Last.of.Us.S02E05.2160p.mkv",
            "total_size": 12_288_000_000,
            "size": 12_288_000_000,
            "uploaded": 3_221_225_472,
            "ratio": 0.41,
            "upspeed": 131_072,
            "dlspeed": 5_242_880,
            "num_leechs": 6,
            "num_incomplete": 19,
            "added_on": 1_760_110_000,
            "save_path": "/media/pt/",
            "eta": 4_860,
            "category": "剧集",
            "tags": "PT,WEB-DL",
            "num_seeds": 27,
            "amount_left": 4_382_208_000,
        },
        {
            "hash": "c" * 40,
            "name": "中国奇谭·Chinese.Fantasy.Anthology.2023.1080p.BluRay.x265.10bit.双语字幕.全八集.归档保留",
            "state": "pausedDL",
            "progress": 0.218,
            "content_path": "/media/pt/中国奇谭·Chinese.Fantasy.Anthology.2023.1080p.BluRay.x265.10bit.双语字幕.全八集.归档保留/中国奇谭.S01E02.mkv",
            "total_size": 8_640_000_000,
            "size": 8_640_000_000,
            "uploaded": 1_048_576,
            "ratio": 0.0,
            "upspeed": 0,
            "dlspeed": 0,
            "num_leechs": 0,
            "num_incomplete": 0,
            "added_on": 1_759_220_000,
            "save_path": "/media/pt/",
            "eta": 86_400,
            "category": "动画",
            "tags": "paused,双语",
            "num_seeds": 4,
            "amount_left": 6_756_480_000,
        },
    ],
    "public": [
        {
            "hash": "d" * 40,
            "name": "流浪地球2·The.Wandering.Earth.II.2023.2160p.UHD.BluRay.HEVC.TrueHD.7.1.Atmos.长文件名公共资源测试",
            "state": "stalledUP",
            "progress": 1.0,
            "content_path": "/media/public/流浪地球2·The.Wandering.Earth.II.2023.2160p.UHD.BluRay.HEVC.TrueHD.7.1.Atmos.长文件名公共资源测试/流浪地球2.mkv",
            "total_size": 31_457_280_000,
            "size": 31_457_280_000,
            "uploaded": 9_876_543_210,
            "ratio": 0.31,
            "upspeed": 262_144,
            "dlspeed": 0,
            "num_leechs": 2,
            "num_incomplete": 5,
            "added_on": 1_758_910_000,
            "save_path": "/media/public/",
            "eta": 0,
            "category": "电影",
            "tags": "public,电影",
            "num_seeds": 33,
            "amount_left": 0,
        },
        {
            "hash": "e" * 40,
            "name": "A.Good.Days.Family.2024.1080p.WEB-DL.AAC2.0.H264-公开下载器回归测试含中文超长标题",
            "state": "downloading",
            "progress": 0.372,
            "content_path": "/media/public/A.Good.Days.Family.2024.1080p.WEB-DL.AAC2.0.H264-公开下载器回归测试含中文超长标题/A.Good.Days.Family.2024.1080p.mkv",
            "total_size": 7_654_321_000,
            "size": 7_654_321_000,
            "uploaded": 876_543_210,
            "ratio": 0.11,
            "upspeed": 65_536,
            "dlspeed": 2_621_440,
            "num_leechs": 11,
            "num_incomplete": 42,
            "added_on": 1_760_280_000,
            "save_path": "/media/public/",
            "eta": 3_942,
            "category": "电影",
            "tags": "proxy,web",
            "num_seeds": 18,
            "amount_left": 4_806_912_000,
        },
        {
            "hash": "f" * 40,
            "name": "Archive.Collection.1999-2024.1080p.多语言字幕.长期保种.暂停任务.English-Chinese-Fixture",
            "state": "pausedDL",
            "progress": 0.087,
            "content_path": "/media/public/Archive.Collection.1999-2024.1080p.多语言字幕.长期保种.暂停任务.English-Chinese-Fixture/Archive.Collection.Part01.mkv",
            "total_size": 5_242_880_000,
            "size": 5_242_880_000,
            "uploaded": 0,
            "ratio": 0.0,
            "upspeed": 0,
            "dlspeed": 0,
            "num_leechs": 0,
            "num_incomplete": 0,
            "added_on": 1_757_110_000,
            "save_path": "/media/public/",
            "eta": 86_400,
            "category": "纪录片",
            "tags": "paused,archive",
            "num_seeds": 1,
            "amount_left": 4_786_034_000,
        },
    ],
}


def _qb_rows(kind: str, mode: str) -> list[dict]:
    if mode == "empty":
        return []
    return [dict(row) for row in QB_TASKS[kind]]


def _qb_preferences(kind: str) -> dict:
    save_path = "/media/pt/" if kind == "pt" else "/media/public/"
    return {
        "up_limit": 1_048_576,
        "save_path": save_path,
        "temp_path_enabled": True,
        "temp_path": save_path + ".incomplete/",
        "incomplete_files_ext": True,
        "preallocate_all": True,
        "max_ratio_enabled": True,
        "max_ratio": 2.5,
        "dl_limit": 0,
        "max_uploads": 12,
        "max_uploads_per_torrent": 4,
        "max_connec": 200,
        "max_connec_per_torrent": 80,
        "proxy_type": "None" if kind == "pt" else "HTTP",
        "listen_port": 52000 if kind == "pt" else 52001,
        "dht": kind != "pt",
        "pex": True,
        "lsd": kind != "pt",
    }


def _qb_transfer(rows: list[dict]) -> dict:
    return {
        "dl_info_speed": sum(int(row["dlspeed"]) for row in rows),
        "up_info_speed": sum(int(row["upspeed"]) for row in rows),
        "free_space_on_disk": 1_500 * 1024**3,
        "up_info_data": 57_392_123_456,
        "dl_info_data": 98_120_654_321,
    }


def _qb_properties(task: dict) -> dict:
    return {
        "total_downloaded": task["total_size"] - task["amount_left"],
        "total_uploaded": task["uploaded"],
        "seeding_time": 86_400 if task["progress"] == 1 else 0,
        "addition_date": task["added_on"],
    }


def _qb_files(task: dict) -> list[dict]:
    completed = task["progress"]
    size = task["total_size"]
    return [
        {"name": task["content_path"], "size": size, "progress": completed,
         "priority": 1, "index": 0},
        {"name": task["name"] + "/README-中文说明.txt", "size": 4096,
         "progress": completed, "priority": 6, "index": 1},
    ]


def _qb_trackers(task: dict) -> list[dict]:
    private = "PT" in task["tags"] or "keep-seeding" in task["tags"]
    return [
        {"url": "https://tracker.qa.example/announce", "status": 2,
         "msg": "", "num_seeds": task["num_seeds"], "num_leeches": task["num_leechs"]},
        {"url": "udp://backup.qa.example:6969/announce", "status": 2 if not private else 0,
         "msg": "This torrent is private" if private else "", "num_seeds": -1, "num_leeches": -1},
    ]


def _qb_peers(task: dict) -> dict:
    if task["state"].startswith("paused"):
        return {"peers": {}, "full_update": True}
    return {"peers": {
        "192.0.2.11": {"ip": "192.0.2.11", "port": 51413, "client": "qBittorrent/5.0 QA",
                        "progress": min(1, task["progress"] + 0.12), "dl_speed": 65536, "up_speed": 131072},
        "198.51.100.22": {"ip": "198.51.100.22", "port": 6881, "client": "Transmission/4.0 QA",
                           "progress": 0.88, "dl_speed": 0, "up_speed": 32768},
    }, "full_update": True}


class PreviewHandler(BaseHTTPRequestHandler):
    """Serve only fixed UI paths and deterministic in-memory fixtures."""

    server_version = "XiaomiPluginPreview/1"

    def log_message(self, *_args):
        # Query strings can contain tokens in ordinary browser requests.  The
        # fixture intentionally emits no request logs.
        return

    def _send(self, status: int, body: bytes, content_type: str,
              headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header(PREVIEW_HEADER, "true")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, value: dict) -> None:
        self._send(status, _json_bytes(value), "application/json; charset=utf-8")

    def _request_mode(self, query: dict[str, list[str]], path: str) -> str:
        direct = query.get("mode", [None])[0]
        if direct in MODES:
            return direct
        referer = self.headers.get("Referer", "")
        referred = parse_qs(urlsplit(referer).query).get("mode", [None])[0]
        if referred in MODES:
            return referred
        # This optional path form is useful when a browser strips referrers:
        # /qa/<mode>/<ui>/... still maps to the same checked-in files.
        parts = path.strip("/").split("/")
        if len(parts) > 1 and parts[0] == "qa" and parts[1] in MODES:
            return parts[1]
        return "ok"

    def _static(self, relative: str, kind: str, mode: str, banner: bool = False) -> None:
        target = _safe_file(relative)
        if target is None:
            self._json(404, {"error": "fixture_asset_not_found"})
            return
        body = target.read_bytes()
        if banner and target.suffix.lower() == ".html":
            native_mock = parse_qs(urlsplit(self.path).query).get("native", [""])[0] == "mock"
            body = _with_banner(body, kind, mode, native_mock=native_mock)
        content_type = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                        ".js": "application/javascript; charset=utf-8", ".svg": "image/svg+xml",
                        ".png": "image/png"}.get(target.suffix.lower(), "application/octet-stream")
        self._send(200, body, content_type)

    def _monitor(self, path: str, query: dict[str, list[str]], mode: str) -> None:
        prefix, _, rest = path.strip("/").partition("/")
        if prefix not in {"resource-monitor", "docker-monitor"}:
            self._json(404, {"error": "fixture_route_not_found"})
            return
        rest = rest or "index.html"
        if rest in {"", "index.html"}:
            self._static("resource-monitor/index.html", "Resource Monitor", mode, banner=True)
            return
        if rest in {"monitor.js", "style.css"}:
            self._static(f"resource-monitor/{rest}", "Resource Monitor", mode)
            return
        if rest != "monitor.cgi":
            self._json(404, {"error": "fixture_route_not_found"})
            return
        asset = query.get("asset", [None])[0]
        if asset in {"monitor.js", "style.css"}:
            self._static(f"resource-monitor/{asset}", "Resource Monitor", mode)
            return
        if asset == "icon.svg":
            self._static("assets/resource-monitor.svg", "Resource Monitor", mode)
            return
        if asset == "docker-icon.svg":
            self._static("assets/docker-monitor.svg", "Resource Monitor", mode)
            return
        if asset == "native-bridge.js":
            self._static("mediastack/native-bridge.js", "Resource Monitor", mode)
            return
        api = query.get("api", [None])[0]
        if api not in {"status", "docker"}:
            self._json(404, {"error": "fixture_route_not_found"})
        elif mode == "error":
            self._json(503, {"error": "preview_fixture_error", "message": "QA fixture error mode"})
        elif api == "status":
            self._json(200, _sample_host(mode))
        else:
            self._json(200, _sample_docker(mode))

    def _music_asset(self, rest: str, mode: str) -> None:
        if rest in {"", "index.html"}:
            self._static("music/index.html", "Music", mode, banner=True)
        elif rest in {"music.js", "music.css"}:
            self._static(f"music/{rest}", "Music", mode)
        elif rest == "icon.svg":
            self._static("assets/music.svg", "Music", mode)
        elif rest == "native-bridge.js":
            self._static("mediastack/native-bridge.js", "Music", mode)
        else:
            self._json(404, {"error": "fixture_route_not_found"})

    @staticmethod
    def _qb_kind(path: str) -> str | None:
        prefix = path.strip("/").split("/", 1)[0]
        return prefix if prefix in {"pt", "public"} else None

    def _qb_session(self, kind: str, mode: str, method: str) -> None:
        if mode == "error":
            self._json(503, {"error": "preview_fixture_error", "message": "QA fixture downloader unavailable"})
        elif method == "DELETE":
            self._json(200, {"ok": True, "preview_fixture": True})
        else:
            # The request body is consumed by do_POST but deliberately never
            # decoded.  The fixed SID is not derived from a submitted secret.
            self._json(200, {"session": f"preview-{kind}-session", "preview_fixture": True})

    def _qb_api(self, kind: str, path: str, query: dict[str, list[str]], mode: str) -> None:
        prefix = f"/{kind}/api/v2/"
        if not path.startswith(prefix):
            self._json(404, {"error": "fixture_route_not_found"})
            return
        endpoint = path[len(prefix):]
        if mode == "error":
            self._json(503, {"error": "preview_fixture_error", "message": "QA fixture downloader unavailable"})
            return
        rows = _qb_rows(kind, mode)
        if endpoint == "torrents/info":
            self._json(200, rows)
            return
        if endpoint == "transfer/info":
            self._json(200, _qb_transfer(rows))
            return
        if endpoint == "app/preferences":
            self._json(200, _qb_preferences(kind))
            return
        if endpoint == "sync/maindata":
            transfer = _qb_transfer(rows)
            self._json(200, {"server_state": transfer, "full_update": True, "rid": 1})
            return

        hash_value = query.get("hash", [""])[0]
        task = next((row for row in rows if row["hash"] == hash_value), None)
        if task is None:
            self._json(404, {"error": "fixture_task_not_found"})
            return
        if endpoint == "torrents/properties":
            self._json(200, _qb_properties(task))
        elif endpoint == "torrents/files":
            self._json(200, _qb_files(task))
        elif endpoint == "torrents/trackers":
            self._json(200, _qb_trackers(task))
        elif endpoint == "sync/torrentPeers":
            self._json(200, _qb_peers(task))
        else:
            self._json(404, {"error": "fixture_route_not_found"})

    def _qb_asset(self, kind: str, rest: str, query: dict[str, list[str]], mode: str) -> None:
        if rest in {"", "index.html"}:
            self._static("mediastack/qb-native.html", f"{kind.upper()} qB", mode, banner=True)
        elif rest in {"qb-native.js", "qb-native.css"}:
            self._static(f"mediastack/{rest}", f"{kind.upper()} qB", mode)
        elif rest == "qb-ui-bridge.js":
            self._static("mediastack/qb-ui-bridge.js", f"{kind.upper()} qB", mode)
        elif rest == "native-bridge.js":
            self._static("mediastack/native-bridge.js", f"{kind.upper()} qB", mode)
        elif rest == "icon.png":
            self._static(f"assets/{'qbpt' if kind == 'pt' else 'qbpublic'}.png", f"{kind.upper()} qB", mode)
        elif rest == "native/session":
            self._qb_session(kind, mode, "GET")
        elif rest.startswith("api/v2/"):
            self._qb_api(kind, "/" + kind + "/" + rest, query, mode)
        else:
            self._json(404, {"error": "fixture_route_not_found"})

    def _music_api(self, query: dict[str, list[str]], mode: str) -> None:
        api = query.get("api", [""])[0]
        # music.js sends api=v1/songs/... while the production CGI accepts the
        # same endpoint after its local api/ prefix.
        api_path = api if api.startswith("api/") else "api/" + api
        if api_path in {"api/status", "api/health", "api/version"}:
            if mode == "error":
                self._json(503, {"error": "preview_fixture_error", "message": "QA fixture Songloft is offline"})
            else:
                self._json(200, {"configured": True, "online": True, "service": "Songloft",
                                 "health": {"status": "ok", "preview_fixture": True}})
            return
        if api_path in {"api/login", "api/refresh"}:
            if mode == "error":
                self._json(503, {"error": "preview_fixture_error", "message": "QA fixture login unavailable"})
            else:
                # The request body is discarded by do_POST without decoding or
                # persisting credentials; these are fixture-only token values.
                self._json(200, {"access_token": "preview-access-token", "refresh_token": "preview-refresh-token",
                                 "expires_in": 3600})
            return
        if api_path == "api/logout":
            self._json(200, {"ok": True, "preview_fixture": True})
            return
        if api_path in {"api/songs", "api/playlists"}:
            if mode == "error":
                self._json(503, {"error": "preview_fixture_error", "message": "QA fixture library unavailable"})
            elif mode == "empty":
                self._json(200, {"songs": [], "total": 0, "preview_fixture": True})
            else:
                keyword = query.get("keyword", [""])[0].lower()
                songs = [song for song in SONGS if not keyword or keyword in " ".join(
                    str(song.get(key, "")) for key in ("title", "artist", "album")).lower()]
                try:
                    offset = max(0, int(query.get("offset", ["0"])[0]))
                    limit = min(40, max(1, int(query.get("limit", ["40"])[0])))
                except ValueError:
                    offset, limit = 0, 40
                self._json(200, {"songs": songs[offset:offset + limit], "total": len(songs),
                                 "preview_fixture": True})
            return
        match = api_path.split("/")
        if len(match) == 5 and match[:3] == ["api", "v1", "songs"] and match[4] == "cover":
            self._send(200, ("<svg xmlns='http://www.w3.org/2000/svg' width='96' height='96'>"
                             "<rect width='96' height='96' fill='#273b71'/><circle cx='48' cy='48' r='26' fill='#f4c95d'/>"
                             "</svg>").encode(), "image/svg+xml")
            return
        if len(match) == 5 and match[:3] == ["api", "v1", "songs"] and match[4] in {"play", "audio-tracks"}:
            self._audio()
            return
        self._json(404, {"error": "fixture_route_not_found"})

    def _audio(self) -> None:
        body = AUDIO_FIXTURE
        start, end = 0, len(body) - 1
        range_header = self.headers.get("Range", "")
        status = 200
        headers = {"Accept-Ranges": "bytes"}
        if range_header.startswith("bytes="):
            spec = range_header[6:].split(",", 1)[0]
            first, separator, last = spec.partition("-")
            try:
                if first:
                    start = int(first)
                    end = int(last) if separator and last else len(body) - 1
                elif separator and last:
                    length = int(last)
                    start = max(0, len(body) - length)
                    end = len(body) - 1
                if start < 0 or start >= len(body) or end < start:
                    raise ValueError()
                end = min(end, len(body) - 1)
                status = 206
                headers["Content-Range"] = f"bytes {start}-{end}/{len(body)}"
            except ValueError:
                self._send(416, b"", "audio/wav", {"Content-Range": f"bytes */{len(body)}"})
                return
        self._send(status, body[start:end + 1], "audio/wav", headers)

    def _resource_center(self, path: str, mode: str) -> None:
        prefix, _, rest = path.strip("/").partition("/")
        if prefix not in {"center", "resource-center"}:
            self._json(404, {"error": "fixture_route_not_found"})
            return
        rest = rest or "index.html"
        if rest in {"", "index.html"}:
            self._static("mediastack/index.html", "Resource Center", mode, banner=True)
        elif rest in {"resource-center.js", "resource-center.css"}:
            self._static(f"mediastack/{rest}", "Resource Center", mode)
        else:
            self._json(404, {"error": "fixture_route_not_found"})

    def _mock_upstream(self, path: str, mode: str) -> None:
        if mode == "error":
            body = ("<meta name='mediastack-error' content='503'>"
                    "<main style='font:16px system-ui;padding:24px'>QA fixture upstream error</main>").encode()
        else:
            title = {"pansou": "盘搜资源 · QA mock", "pt": "PT 下载器 · QA mock",
                     "public": "公共下载器 · QA mock"}.get(path.strip("/").split("/", 1)[0], "QA mock")
            body = ("<!doctype html><meta name='viewport' content='width=device-width'>"
                    "<title>" + title + "</title>"
                    "<main style='font:16px system-ui;padding:24px'>"
                    "<div style='display:inline-block;padding:6px 9px;background:#fff3bf;border-radius:8px'>"
                    "QA FIXTURE · 模拟上游</div><h1>" + title + "</h1>"
                    "<p>这是本地预览 mock upstream，用于验证资源中心切换。</p></main>").encode()
        self._send(200 if mode != "error" else 503, body, "text/html; charset=utf-8")

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        if "\\" in path or ".." in path.split("/"):
            self._json(404, {"error": "fixture_route_not_found"})
            return
        query = parse_qs(parsed.query, keep_blank_values=True)
        mode = self._request_mode(query, path)
        if path.startswith(("/resource-monitor/", "/docker-monitor/")):
            self._monitor(path, query, mode)
        elif path.startswith(("/music/",)):
            prefix, _, rest = path.strip("/").partition("/")
            rest = rest or "index.html"
            if rest == "music.cgi":
                if query.get("asset", [None])[0] in {"music.js", "music.css", "icon.svg", "native-bridge.js"}:
                    self._music_asset(query["asset"][0], mode)
                elif "api" in query:
                    self._music_api(query, mode)
                else:
                    self._music_asset("index.html", mode)
            else:
                self._music_asset(rest, mode)
        elif path.startswith(("/center/", "/resource-center/")):
            self._resource_center(path, mode)
        elif path == "/native-bridge.js":
            self._static("mediastack/native-bridge.js", "Native Bridge", mode)
        elif path.startswith("/assets/"):
            self._static(path.lstrip("/"), "Shared Asset", mode)
        elif path.startswith(("/pt/", "/public/")):
            kind = self._qb_kind(path)
            rest = path.strip("/").split("/", 1)[1] if "/" in path.strip("/") else ""
            self._qb_asset(kind, rest, query, mode)
        elif path.startswith("/pansou/"):
            self._mock_upstream(path, mode)
        else:
            self._json(404, {"error": "fixture_route_not_found"})

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        mode = self._request_mode(query, path)
        # Consume but never parse or persist login bodies.  This keeps the
        # connection usable while ensuring no credential is read by the fixture.
        try:
            length = max(0, min(int(self.headers.get("Content-Length", "0") or "0"), 1024 * 1024))
        except ValueError:
            length = 0
        if length:
            self.rfile.read(length)
        kind = self._qb_kind(path)
        if kind and path.rstrip("/").endswith("/native/session"):
            self._qb_session(kind, mode, "POST")
            return
        if kind and path.startswith(f"/{kind}/api/v2/"):
            self._json(405, {"error": "fixture_read_only"})
            return
        if path.startswith("/music/") and path.rstrip("/").endswith("/music.cgi") and query.get("api", [""])[0] in {
                "login", "refresh", "logout"}:
            self._music_api(query, mode)
            return
        self._json(405, {"error": "fixture_read_only"})

    def do_DELETE(self):  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        mode = self._request_mode(query, path)
        kind = self._qb_kind(path)
        if kind and path.rstrip("/").endswith("/native/session"):
            self._qb_session(kind, mode, "DELETE")
            return
        self._json(405, {"error": "fixture_read_only"})


class PreviewServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765, help="loopback port (0 chooses an ephemeral port)")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    server = PreviewServer((HOST, args.port), PreviewHandler)
    port = server.server_address[1]
    print(f"QA fixture server: http://{HOST}:{port}", flush=True)
    print(f"  monitor ok:    http://{HOST}:{port}/resource-monitor/index.html?mode=ok", flush=True)
    print(f"  monitor empty: http://{HOST}:{port}/resource-monitor/index.html?mode=empty#docker", flush=True)
    print(f"  monitor error: http://{HOST}:{port}/resource-monitor/index.html?mode=error", flush=True)
    print(f"  center ok:     http://{HOST}:{port}/center/index.html?mode=ok", flush=True)
    print(f"  center error:  http://{HOST}:{port}/center/index.html?mode=error", flush=True)
    print(f"  PT qB ok:      http://{HOST}:{port}/pt/index.html?mode=ok", flush=True)
    print(f"  public qB ok:   http://{HOST}:{port}/public/index.html?mode=ok", flush=True)
    print(f"  qB empty:       http://{HOST}:{port}/public/index.html?mode=empty", flush=True)
    print(f"  qB error:        http://{HOST}:{port}/public/index.html?mode=error", flush=True)
    print(f"  music ok:      http://{HOST}:{port}/music/index.html?mode=ok", flush=True)
    print(f"  music empty:   http://{HOST}:{port}/music/index.html?mode=empty", flush=True)
    print(f"  music error:   http://{HOST}:{port}/music/index.html?mode=error", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
