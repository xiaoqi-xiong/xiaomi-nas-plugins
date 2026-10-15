#!/usr/bin/env python3
"""Owner-only read-only system resource dashboard CGI."""

import importlib.util
import json
import mimetypes
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit


ROOT = Path(__file__).resolve().parent
APP_ROOT = ROOT.parent
sys.path.insert(0, str(APP_ROOT))


def respond(status: str, content_type: str, body: str | bytes) -> None:
    if isinstance(body, str):
        body = body.encode("utf-8")
    header_type = content_type if ";" in content_type else content_type + "; charset=utf-8"
    sys.stdout.buffer.write((f"Status: {status}\r\nContent-Type: {header_type}\r\n"
                             f"Cache-Control: no-store\r\nContent-Length: {len(body)}\r\n\r\n").encode()
                            + body)


def json_error(status: str, message: str) -> None:
    respond(status, "application/json", json.dumps({"error": message}, ensure_ascii=False))


def _route_path(uri: str) -> tuple[str, str]:
    parsed = urlsplit(uri)
    path = unquote(parsed.path).lstrip("/")
    # The deployment catalog exposes the same handler under both entries;
    # native shells consequently send either directory prefix in FILE_URI.
    for marker in ("resource-monitor/", "docker-monitor/"):
        if marker in path:
            path = path.split(marker, 1)[1]
            break
    query = parsed.query or os.environ.get("QUERY_STRING", "")
    return path, query


def _content_type(asset: str) -> str:
    if asset.endswith(".js"):
        return "application/javascript"
    if asset.endswith(".css"):
        return "text/css"
    if asset.endswith(".html"):
        return "text/html"
    return mimetypes.guess_type(asset)[0] or "application/octet-stream"


def _serve_asset(asset: str) -> None:
    local_assets = {"index.html": ROOT / "index.html",
                    "monitor.js": ROOT / "monitor.js",
                    "style.css": ROOT / "style.css"}
    if asset == "icon.svg":
        target = APP_ROOT / "assets" / "resource-monitor.svg"
    elif asset == "docker-icon.svg":
        target = APP_ROOT / "assets" / "docker-monitor.svg"
    elif asset == "native-bridge.js":
        target = APP_ROOT / "mediastack" / "native-bridge.js"
    else:
        target = local_assets.get(asset)
    if target is None or not target.is_file():
        respond("404 Not Found", "text/plain", "Unknown resource monitor asset")
        return
    try:
        respond("200 OK", _content_type(asset), target.read_bytes())
    except OSError:
        json_error("500 Internal Server Error", "Resource monitor asset unavailable")


def _serve_api(api: str) -> None:
    try:
        if api == "status":
            state_file = APP_ROOT / "private" / "resource-monitor.json"
            module = _load_module()
            body = module.collect_status(state_file=state_file)
        elif api == "docker":
            module = _load_module()
            body = module._docker()
        else:
            json_error("404 Not Found", "Unknown resource monitor query")
            return
        respond("200 OK", "application/json", json.dumps(body, ensure_ascii=False))
    except Exception:
        # Native CGI output must remain valid JSON and must not reveal paths,
        # settings, command output, or Python tracebacks.
        json_error("500 Internal Server Error", "Resource monitor collection unavailable")


def _load_module():
    spec = importlib.util.spec_from_file_location("resource_monitor", ROOT / "monitor.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("monitor module unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    try:
        from settings import get_settings, owner_is_valid
        settings = get_settings(APP_ROOT)
        owner_ok = owner_is_valid(settings, os.environ.get("USER", ""), os.getuid())
    except Exception:
        json_error("500 Internal Server Error", "Resource monitor configuration unavailable")
        return
    if not owner_ok:
        json_error("403 Forbidden", "Plugin owner login required")
        return
    if os.environ.get("REQUEST_METHOD", "GET").upper() != "GET":
        json_error("405 Method Not Allowed", "Read-only endpoint")
        return

    path, raw_query = _route_path(os.environ.get("FILE_URI", ""))
    if ".." in path.split("/") or "\\" in path:
        respond("404 Not Found", "text/plain", "Unknown resource monitor route")
        return
    query = parse_qs(raw_query, keep_blank_values=True)
    if any(key not in {"api", "asset"} for key in query):
        respond("404 Not Found", "text/plain", "Unknown resource monitor query")
        return
    if "api" in query and "asset" in query:
        respond("404 Not Found", "text/plain", "Unknown resource monitor query")
        return
    if any(len(values) != 1 for values in query.values()):
        respond("404 Not Found", "text/plain", "Unknown resource monitor query")
        return

    if "api" in query:
        if path not in {"", "index.html", "monitor.cgi"} or query["api"][0] not in {"status", "docker"}:
            respond("404 Not Found", "text/plain", "Unknown resource monitor route")
            return
        _serve_api(query["api"][0])
        return
    if "asset" in query:
        if path not in {"", "index.html", "monitor.cgi"} or query["asset"][0] not in {
                "monitor.js", "style.css", "native-bridge.js", "icon.svg", "docker-icon.svg"}:
            respond("404 Not Found", "text/plain", "Unknown resource monitor route")
            return
        _serve_asset(query["asset"][0])
        return

    if path in {"", "index.html", "monitor.cgi"}:
        _serve_asset("index.html")
    elif path in {"monitor.js", "style.css", "native-bridge.js", "icon.svg", "docker-icon.svg"}:
        _serve_asset(path)
    else:
        respond("404 Not Found", "text/plain", "Unknown resource monitor route")


if __name__ == "__main__":
    main()
