#!/usr/bin/env python3
"""Owner-scoped music downloads for the fixed public qBittorrent service.

This module intentionally has no CGI framework dependency.  ``MusicDownloads``
is the small boundary used by the Music CGI and by the owner timer: it accepts
validated magnets, records only hash keyed admission state, and imports files
from a fixed qB path into a fixed Songloft library without touching downloader
source files.
"""

from __future__ import annotations

import base64
import binascii
from contextlib import contextmanager
import fcntl
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import time
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, urlencode, urlsplit


TAG = "native-music"
MIN_FREE_BYTES = 10 * 1024**3
MAX_MAGNET_BYTES = 16 * 1024
MAX_STATUS_TASKS = 128
MAX_IMPORT_FILES = 128
MAX_IMPORT_BYTES = 2 * 1024**3
SCAN_MARKER = ".native-music.json"
STAGING_DIR = ".native-music-staging"
LEDGER_NAME = "music-downloads.json"
LOCK_NAME = "music-downloads.lock"
AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".aiff", ".alac"}
COMPLETE_STATES = {
    "uploading",
    "stalledup",
    "pausedup",
    "queuedup",
    "forcedup",
    "stoppedup",
    "seeding",
}
_HEX40 = re.compile(r"^[0-9a-f]{40}$", re.I)
_BASE32 = re.compile(r"^[a-z2-7]{32}$", re.I)


def _qb_host(settings: dict[str, Any]) -> str:
    """Return the fixed qB host, keeping Songloft's NAS host independent."""
    music = settings.get("music") if isinstance(settings.get("music"), dict) else {}
    candidate = music.get("qb_host") or settings.get("nas_host") or "127.0.0.1"
    if not isinstance(candidate, str):
        raise MusicDownloadError(503, "公共下载器主机配置无效", "qb_unconfigured")
    host = candidate.strip()
    # Permit DNS names, IPv4, and bracketed IPv6 literals, but never a URL,
    # credentials, path, or control character from settings.
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if (not host or len(host) > 253 or host.startswith(".") or host.endswith(".")
                or any(ord(char) <= 32 for char in host)
                or any(char in host for char in "/?#@\\")):
            raise MusicDownloadError(503, "公共下载器主机配置无效", "qb_unconfigured")
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,251}[A-Za-z0-9])?", host):
            raise MusicDownloadError(503, "公共下载器主机配置无效", "qb_unconfigured")
    return host


class _BencodeReader:
    """Bounded bencode reader retaining raw spans for the v1 info hash."""

    def __init__(self, data: bytes):
        self.data = data
        self.steps = 0
        self.info_span: tuple[int, int] | None = None

    def parse(self, position: int = 0, depth: int = 0) -> tuple[Any, int, int]:
        self.steps += 1
        if self.steps > 500_000 or depth > 64 or position >= len(self.data):
            raise MusicDownloadError(400, "torrent 文件格式无效", "invalid_torrent")
        start = position
        marker = self.data[position:position + 1]
        if marker == b"i":
            end = self.data.find(b"e", position + 1)
            if end < 0 or end - position > 32:
                raise MusicDownloadError(400, "torrent 文件格式无效", "invalid_torrent")
            number = self.data[position + 1:end]
            if not re.fullmatch(rb"-?(0|[1-9][0-9]*)", number):
                raise MusicDownloadError(400, "torrent 文件格式无效", "invalid_torrent")
            try:
                value = int(number)
            except ValueError as exc:
                raise MusicDownloadError(400, "torrent 文件格式无效", "invalid_torrent") from exc
            return value, start, end + 1
        if marker == b"l" or marker == b"d":
            position += 1
            if marker == b"l":
                value: list[Any] = []
                while position < len(self.data) and self.data[position:position + 1] != b"e":
                    item, _, position = self.parse(position, depth + 1)
                    value.append(item)
                if position >= len(self.data):
                    raise MusicDownloadError(400, "torrent 文件格式无效", "invalid_torrent")
                return value, start, position + 1
            value_dict: dict[bytes, Any] = {}
            while position < len(self.data) and self.data[position:position + 1] != b"e":
                key, _, position = self.parse(position, depth + 1)
                if not isinstance(key, bytes) or key in value_dict:
                    raise MusicDownloadError(400, "torrent 文件格式无效", "invalid_torrent")
                item_start = position
                item, _, position = self.parse(position, depth + 1)
                if depth == 0 and key == b"info":
                    self.info_span = (item_start, position)
                value_dict[key] = item
            if position >= len(self.data):
                raise MusicDownloadError(400, "torrent 文件格式无效", "invalid_torrent")
            return value_dict, start, position + 1
        if marker.isdigit():
            colon = self.data.find(b":", position, min(len(self.data), position + 16))
            if colon < 0:
                raise MusicDownloadError(400, "torrent 文件格式无效", "invalid_torrent")
            length_bytes = self.data[position:colon]
            if not re.fullmatch(rb"(0|[1-9][0-9]*)", length_bytes):
                raise MusicDownloadError(400, "torrent 文件格式无效", "invalid_torrent")
            length = int(length_bytes)
            end = colon + 1 + length
            if length > 2 * 1024 * 1024 or end > len(self.data):
                raise MusicDownloadError(400, "torrent 文件过大", "invalid_torrent")
            return self.data[colon + 1:end], start, end
        raise MusicDownloadError(400, "torrent 文件格式无效", "invalid_torrent")


def _torrent_v1_hash(data: bytes) -> str:
    if not isinstance(data, bytes) or not data or len(data) > 2 * 1024 * 1024:
        raise MusicDownloadError(400, "torrent 文件过大或为空", "invalid_torrent")
    reader = _BencodeReader(data)
    root, _, end = reader.parse()
    if end != len(data) or not isinstance(root, dict) or not isinstance(root.get(b"info"), dict):
        raise MusicDownloadError(400, "torrent 文件缺少有效种子信息", "invalid_torrent")
    info = root[b"info"]
    # A v1 info dictionary has a piece length, piece hashes, and either one
    # file length or a files list.  The raw span must be found separately so
    # dictionary re-encoding can never change the hash.
    if not isinstance(info.get(b"piece length"), int) or not isinstance(info.get(b"pieces"), bytes):
        raise MusicDownloadError(400, "torrent 文件不含 v1 音频种子信息", "invalid_torrent")
    if not isinstance(info.get(b"name"), bytes) or (not isinstance(info.get(b"length"), int) and not isinstance(info.get(b"files"), list)):
        raise MusicDownloadError(400, "torrent 文件信息不完整", "invalid_torrent")
    if reader.info_span is None:
        raise MusicDownloadError(400, "torrent 文件格式无效", "invalid_torrent")
    parsed_start, parsed_end = reader.info_span
    return hashlib.sha1(data[parsed_start:parsed_end]).hexdigest()


class MusicDownloadError(Exception):
    """Expected error with a safe status and human-facing message."""

    def __init__(self, status: int, message: str, code: str = "music_download_error"):
        super().__init__(message)
        self.status = int(status)
        self.code = code
        self.message = message


def _safe_error(exc: BaseException) -> MusicDownloadError:
    if isinstance(exc, MusicDownloadError):
        return exc
    if isinstance(exc, (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, http.client.HTTPException)):
        return MusicDownloadError(503, "下载器或音乐库暂时不可用", "music_backend_unavailable")
    return MusicDownloadError(503, "音乐下载任务暂时不可用", "music_download_error")


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _copy_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _normal_hash(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    return candidate if _HEX40.fullmatch(candidate) else None


def parse_magnet(magnet: str) -> tuple[str, str]:
    """Return ``(canonical_v1_hash, original_magnet)`` after strict checks."""
    if not isinstance(magnet, str):
        raise MusicDownloadError(400, "请输入有效的磁力链接", "invalid_magnet")
    encoded = magnet.encode("utf-8", "strict")
    if len(encoded) > MAX_MAGNET_BYTES or not magnet.lower().startswith("magnet:?"):
        raise MusicDownloadError(400, "请输入有效的磁力链接", "invalid_magnet")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in magnet):
        raise MusicDownloadError(400, "磁力链接包含无效字符", "invalid_magnet")
    try:
        parsed = urlsplit(magnet)
    except ValueError as exc:
        raise MusicDownloadError(400, "请输入有效的磁力链接", "invalid_magnet") from exc
    if parsed.scheme.lower() != "magnet" or parsed.path not in ("", "?") or parsed.fragment:
        raise MusicDownloadError(400, "请输入有效的磁力链接", "invalid_magnet")
    try:
        values = parse_qs(parsed.query, keep_blank_values=False, strict_parsing=False)
    except ValueError as exc:
        raise MusicDownloadError(400, "磁力链接参数无效", "invalid_magnet") from exc
    hashes: list[str] = []
    if any(ord(char) < 0x20 or ord(char) == 0x7F
           for value in values.values() for item in value for char in item):
        raise MusicDownloadError(400, "磁力链接包含无效字符", "invalid_magnet")
    for item in values.get("xt", []):
        lower = item.lower()
        if not lower.startswith("urn:btih:"):
            continue
        candidate = item[9:]
        if _HEX40.fullmatch(candidate):
            hashes.append(candidate.lower())
        elif _BASE32.fullmatch(candidate):
            try:
                hashes.append(base64.b32decode(candidate.upper()).hex())
            except (ValueError, binascii.Error):  # pragma: no cover - defensive decoder guard
                continue
    if not hashes:
        raise MusicDownloadError(400, "磁力链接缺少有效的种子信息", "invalid_magnet")
    # One canonical key prevents a magnet carrying duplicate/different xts
    # from being admitted ambiguously.
    if len(set(hashes)) != 1:
        raise MusicDownloadError(400, "磁力链接包含多个种子信息", "invalid_magnet")
    return hashes[0], magnet


def _row_hashes(row: dict[str, Any]) -> set[str]:
    answer: set[str] = set()
    for key in ("hash", "infohash_v1", "infohash_v2"):
        value = _normal_hash(row.get(key))
        if value:
            answer.add(value)
    return answer


def _tagged(tags: Any) -> bool:
    if isinstance(tags, str):
        return TAG in {item.strip() for item in tags.split(",")}
    if isinstance(tags, (list, tuple, set)):
        return TAG in tags
    return False


def _state_complete(row: dict[str, Any]) -> bool:
    try:
        progress = float(row.get("progress", 0))
    except (TypeError, ValueError):
        return False
    state = str(row.get("state", "")).replace("_", "").lower()
    return progress >= 1.0 and state in COMPLETE_STATES


def _response_accepted(result: Any) -> bool:
    if isinstance(result, str):
        return result.strip().lower() in ("", "ok.", "ok")
    if isinstance(result, dict):
        if str(result.get("error", "")).strip():
            return False
        try:
            failure = int(result.get("failure_count", 0) or 0)
            successes = int(result.get("success_count", 0) or 0)
            pending = int(result.get("pending_count", 0) or 0)
            if failure:
                return False
            if successes or pending:
                return True
        except (TypeError, ValueError):
            pass
        # A few qB versions return a plain status object with no counters.
        status = str(result.get("status", "")).lower()
        return status in {"ok", "ok.", "accepted", "added"}
    return False


class QbClient:
    """Minimal fixed-host qBittorrent Web API client.

    It deliberately has no arbitrary URL or endpoint surface.  Tests can pass
    a fake object to ``MusicDownloads(qb=...)``; production uses this client.
    """

    def __init__(self, settings: dict[str, Any], app_root: Path):
        self.settings = settings
        self.app_root = Path(app_root)
        ports = settings.get("ports") if isinstance(settings.get("ports"), dict) else {}
        try:
            self.port = int(ports.get("public", 18081))
        except (TypeError, ValueError) as exc:
            raise MusicDownloadError(503, "公共下载器配置无效", "qb_unconfigured") from exc
        if not 1 <= self.port <= 65535:
            raise MusicDownloadError(503, "公共下载器配置无效", "qb_unconfigured")
        self.host = _qb_host(settings)
        self.cookie_name = "QBT_SID_{}".format(self.port)
        self._cookie = ""

    def _credentials(self) -> dict[str, str]:
        path = self.app_root / "private" / "native-login.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            credentials = value.get("public") if isinstance(value, dict) else None
            if not isinstance(credentials, dict):
                raise ValueError
            username, password = credentials.get("username"), credentials.get("password")
            if not isinstance(username, str) or not isinstance(password, str) or not username or not password:
                raise ValueError
            return {"username": username, "password": password}
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise MusicDownloadError(401, "请先登录公共下载器并开启记住登录", "qb_login_required") from exc

    def _connection(self) -> http.client.HTTPConnection:
        return http.client.HTTPConnection(self.host, self.port, timeout=12)

    def _login(self) -> None:
        credentials = self._credentials()
        connection = self._connection()
        try:
            body = urlencode(credentials).encode("utf-8")
            connection.request("POST", "/api/v2/auth/login", body=body,
                               headers={"Content-Type": "application/x-www-form-urlencoded"})
            response = connection.getresponse()
            response.read()
            if response.status not in (200, 204):
                raise MusicDownloadError(401, "公共下载器登录未通过", "qb_login_required")
            cookie = ""
            for key, value in response.getheaders():
                if key.lower() != "set-cookie" or not value.startswith(self.cookie_name + "="):
                    continue
                cookie = value.split(";", 1)[0]
                break
            if not cookie:
                raise MusicDownloadError(503, "公共下载器登录响应无效", "qb_unavailable")
            self._cookie = cookie
        except MusicDownloadError:
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise MusicDownloadError(503, "公共下载器暂时不可用", "qb_unavailable") from exc
        finally:
            connection.close()

    def _request(self, endpoint: str, method: str = "GET", values: dict[str, Any] | None = None,
                 query: dict[str, Any] | None = None) -> Any:
        if endpoint not in {"app/preferences", "sync/maindata", "torrents/info", "torrents/files", "torrents/add"}:
            raise MusicDownloadError(500, "下载器请求范围无效", "qb_internal_error")
        if not self._cookie:
            self._login()
        path = "/api/v2/" + endpoint
        if query:
            path += "?" + urlencode(query)
        body = None
        headers = {"Accept": "application/json", "Cookie": self._cookie}
        if values is not None:
            body = urlencode(values).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        connection = self._connection()
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            if response.status == 403:
                self._cookie = ""
            if response.status < 200 or response.status >= 300:
                raise MusicDownloadError(503, "公共下载器请求失败", "qb_unavailable")
            if not raw:
                return ""
            try:
                return json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return raw.decode("utf-8", "replace")
        except MusicDownloadError:
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise MusicDownloadError(503, "公共下载器暂时不可用", "qb_unavailable") from exc
        finally:
            connection.close()

    def preferences(self) -> dict[str, Any]:
        value = self._request("app/preferences")
        if not isinstance(value, dict):
            raise MusicDownloadError(503, "公共下载器返回无效配置", "qb_unavailable")
        return value

    def maindata(self) -> dict[str, Any]:
        value = self._request("sync/maindata")
        return value if isinstance(value, dict) else {}

    def info(self) -> list[dict[str, Any]]:
        value = self._request("torrents/info")
        if not isinstance(value, list):
            raise MusicDownloadError(503, "公共下载器返回无效任务", "qb_unavailable")
        return [item for item in value if isinstance(item, dict)]

    def files(self, torrent_hash: str) -> list[dict[str, Any]]:
        value = self._request("torrents/files", query={"hash": torrent_hash})
        if not isinstance(value, list):
            raise MusicDownloadError(503, "公共下载器返回无效文件列表", "qb_unavailable")
        return [item for item in value if isinstance(item, dict)]

    def add(self, magnet: str, save_path: str) -> Any:
        return self._request("torrents/add", method="POST", values={
            "urls": magnet,
            "savepath": save_path,
            "tags": TAG,
            "autoTMM": "false",
            "stopped": "false",
        })

    def add_torrent(self, torrent: bytes, save_path: str) -> Any:
        """Upload bounded torrent bytes without accepting a URL or path."""
        if not isinstance(torrent, bytes) or len(torrent) > 2 * 1024 * 1024:
            raise MusicDownloadError(400, "torrent 文件过大", "invalid_torrent")
        if not self._cookie:
            self._login()
        boundary = "----native-music-" + os.urandom(12).hex()
        chunks = []
        for key, value in (("savepath", save_path), ("tags", TAG), ("autoTMM", "false"), ("stopped", "false")):
            chunks.extend([
                ("--" + boundary + "\r\n").encode(),
                ('Content-Disposition: form-data; name="{}"\r\n\r\n'.format(key)).encode(),
                str(value).encode("utf-8"), b"\r\n",
            ])
        chunks.extend([
            ("--" + boundary + "\r\n").encode(),
            b'Content-Disposition: form-data; name="torrents"; filename="music.torrent"\r\n',
            b"Content-Type: application/x-bittorrent\r\n\r\n", torrent, b"\r\n",
            ("--" + boundary + "--\r\n").encode(),
        ])
        body = b"".join(chunks)
        connection = self._connection()
        try:
            connection.request("POST", "/api/v2/torrents/add", body=body,
                               headers={"Accept": "application/json", "Cookie": self._cookie,
                                        "Content-Type": "multipart/form-data; boundary=" + boundary,
                                        "Content-Length": str(len(body))})
            response = connection.getresponse()
            raw = response.read()
            if response.status == 403:
                self._cookie = ""
            if response.status < 200 or response.status >= 300:
                raise MusicDownloadError(503, "公共下载器请求失败", "qb_unavailable")
            if not raw:
                return ""
            try:
                return json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return raw.decode("utf-8", "replace")
        except MusicDownloadError:
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise MusicDownloadError(503, "公共下载器暂时不可用", "qb_unavailable") from exc
        finally:
            connection.close()


class MusicDownloads:
    """Admission and bounded import boundary for public music downloads."""

    def __init__(self, settings: dict[str, Any], app_root: Path, qb: Any | None = None):
        self.settings = settings if isinstance(settings, dict) else {}
        self.app_root = Path(app_root)
        music = self.settings.get("music")
        if not isinstance(music, dict):
            raise MusicDownloadError(503, "音乐下载目录尚未配置", "music_unconfigured")
        required = ("download_root", "download_qb_path", "library_path")
        if any(not music.get(key) for key in required):
            raise MusicDownloadError(503, "音乐下载目录尚未配置", "music_unconfigured")
        self.download_root = Path(str(music["download_root"])).expanduser()
        self.download_qb_path = str(music["download_qb_path"]).rstrip("/") or "/"
        self.library_path = Path(str(music["library_path"])).expanduser()
        self.private = self.app_root / "private"
        self.ledger_path = self.private / LEDGER_NAME
        self.lock_path = self.private / LOCK_NAME
        self.submit_lock_path = self.private / "music-downloads-submit.lock"
        self.run_lock_path = self.private / "music-downloads-run.lock"
        self.qb = qb if qb is not None else QbClient(self.settings, self.app_root)

    @contextmanager
    def _exclusive_lock(self, path: Path):
        self.private.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.chmod(path, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _submission_lock(self):
        return self._exclusive_lock(self.submit_lock_path)

    def _run_lock(self):
        return self._exclusive_lock(self.run_lock_path)

    def _read_ledger_unlocked(self) -> dict[str, Any]:
        if not self.ledger_path.exists():
            return {"version": 1, "tasks": {}}
        try:
            value = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise MusicDownloadError(503, "音乐任务记录暂时不可用", "ledger_unavailable") from exc
        if not isinstance(value, dict) or not isinstance(value.get("tasks", {}), dict):
            raise MusicDownloadError(503, "音乐任务记录格式无效", "ledger_unavailable")
        tasks: dict[str, Any] = {}
        for key, task in value.get("tasks", {}).items():
            normalized = _normal_hash(key)
            if normalized and isinstance(task, dict):
                tasks[normalized] = task
        return {"version": 1, "tasks": tasks}

    def _ledger_snapshot(self) -> dict[str, Any]:
        self.private.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.chmod(self.lock_path, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_SH)
            return self._read_ledger_unlocked()
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _ledger_update(self, updater: Callable[[dict[str, Any]], Any]) -> Any:
        self.private.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.chmod(self.lock_path, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            ledger = self._read_ledger_unlocked()
            result = updater(ledger)
            temporary = self.ledger_path.with_name(self.ledger_path.name + ".tmp")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(ledger, stream, ensure_ascii=False, separators=(",", ":"))
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException:
                try:
                    os.close(fd)
                except OSError:
                    pass
                raise
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.ledger_path)
            return result
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _task(self, ledger: dict[str, Any], torrent_hash: str) -> dict[str, Any] | None:
        value = ledger.get("tasks", {}).get(torrent_hash)
        return value if isinstance(value, dict) else None

    @staticmethod
    def _task_state(task: dict[str, Any]) -> str:
        state = task.get("state")
        return state if state in {"pending", "admitted", "imported"} else "pending"

    def _save_pending(self, torrent_hash: str, save_path: str) -> None:
        def update(ledger: dict[str, Any]) -> None:
            task = ledger["tasks"].get(torrent_hash)
            if not isinstance(task, dict):
                ledger["tasks"][torrent_hash] = {
                    "state": "pending", "save_path": save_path, "tag": TAG,
                    "created_at": int(time.time()), "stable": {},
                    "import": {"state": "waiting", "scan_pending": False},
                }
            elif task.get("state") == "pending":
                task["save_path"] = save_path
                task["tag"] = TAG
        self._ledger_update(update)

    def _admit(self, torrent_hash: str) -> None:
        def update(ledger: dict[str, Any]) -> None:
            task = ledger["tasks"].get(torrent_hash)
            if not isinstance(task, dict):
                return
            task["state"] = "admitted"
            task.setdefault("stable", {})
            task.setdefault("import", {"state": "waiting", "scan_pending": False})
        self._ledger_update(update)

    def _mark_import(self, torrent_hash: str, payload: dict[str, Any]) -> None:
        def update(ledger: dict[str, Any]) -> None:
            task = ledger["tasks"].get(torrent_hash)
            if not isinstance(task, dict):
                return
            current = task.setdefault("import", {})
            if isinstance(current, dict):
                current.update(payload)
                if payload.get("state") == "imported":
                    current["message"] = ""
            else:
                task["import"] = payload
        self._ledger_update(update)

    def _public_save_path(self, prefs: dict[str, Any]) -> str:
        # qB's global save_path is intentionally not trusted or changed.  New
        # music tasks always use the configured public music subdirectory.
        save_path = self.download_qb_path
        if (not isinstance(save_path, str) or not save_path.startswith("/") or
                "\x00" in save_path or "\\" in save_path or ".." in save_path.split("/")):
            raise MusicDownloadError(503, "公共下载器保存目录无效", "qb_unavailable")
        return save_path

    @staticmethod
    def _proxy_ok(value: Any) -> bool:
        return isinstance(value, str) and value.strip().lower() in {"socks5", "socks5h", "socks 5", "socks5 proxy"}

    def options(self) -> dict[str, Any]:
        """Return the fixed safe paths and qB save path for the UI."""
        try:
            prefs = self.qb.preferences()
            save_path = self._public_save_path(prefs)
            return {"save_path": save_path, "library_path": str(self.library_path)}
        except Exception as exc:
            raise _safe_error(exc) from exc

    def _submit_parsed(self, torrent_hash: str, add_callback: Callable[[str], Any], kind: str) -> dict[str, Any]:
        ledger = self._ledger_snapshot()
        task = self._task(ledger, torrent_hash)
        try:
            rows = self.qb.info()
            found = next((row for row in rows if torrent_hash in _row_hashes(row)), None)
            if task is None and found is not None:
                raise MusicDownloadError(409, "该下载任务已存在，未由音乐入口提交", "existing_task_conflict")
            if task is not None:
                # Reconcile a response lost after qB accepted the add before
                # attempting another add.  If qB has no matching row, this is
                # an explicit retry and is safe to submit again under the
                # serialized submission lock.
                if found is not None and (str(found.get("save_path", "")) != str(task.get("save_path", "")) or
                                          not _tagged(found.get("tags"))):
                    raise MusicDownloadError(409, "待确认的音乐任务参数不匹配", "admission_conflict")
                if found is not None:
                    self._admit(torrent_hash)
                    # A .torrent may supply the missing metadata of a magnet
                    # already admitted through this queue. qB handles the
                    # duplicate hash; never remove the task or its files.
                    if kind == "torrent 文件" and "meta" in str(found.get("state", "")).lower():
                        if not _response_accepted(add_callback(str(task["save_path"]))):
                            raise MusicDownloadError(503, "下载器未接受种子元数据", "qb_add_rejected")
                        updated = next((row for row in self.qb.info() if torrent_hash in _row_hashes(row)), {})
                        status = "metadata_pending" if "meta" in str(updated.get("state", "")).lower() else "metadata_added"
                        return {"status": status, "hash": torrent_hash, "savepath": task["save_path"]}
                    return {"status": "exists", "hash": torrent_hash,
                            "savepath": task.get("save_path", "")}
            prefs = self.qb.preferences()
            save_path = self._public_save_path(prefs)
            if prefs.get("up_limit") != 1048576 or not self._proxy_ok(prefs.get("proxy_type")):
                raise MusicDownloadError(409, "公共下载器限速或代理设置已变化", "qb_preferences_changed")
            state = self.qb.maindata().get("server_state", {})
            try:
                free = int(state.get("free_space_on_disk", 0))
            except (TypeError, ValueError):
                free = 0
            remaining = sum(max(0, int(row.get("amount_left", 0) or 0)) for row in rows)
            if free - remaining < MIN_FREE_BYTES:
                raise MusicDownloadError(409, "现有下载完成后不足 10 GiB 空间，暂不新增", "insufficient_space")
            # Persist before the network call so an accepted add whose response
            # is lost is reconciled on the next timer/request.
            self._save_pending(torrent_hash, save_path)
            result = add_callback(save_path)
            if not _response_accepted(result):
                raise MusicDownloadError(503, "公共下载器未接受此{}".format(kind), "qb_add_rejected")
            rows = self.qb.info()
            found = next((row for row in rows if torrent_hash in _row_hashes(row)), None)
            if found is not None:
                if str(found.get("save_path", "")) != save_path or not _tagged(found.get("tags")):
                    raise MusicDownloadError(409, "下载器未按音乐入口固定参数添加任务", "admission_conflict")
                self._admit(torrent_hash)
                return {"status": "added", "hash": torrent_hash, "savepath": save_path}
            return {"status": "added", "hash": torrent_hash, "savepath": save_path}
        except MusicDownloadError:
            raise
        except Exception as exc:
            raise _safe_error(exc) from exc

    def _submit_magnet_parsed(self, torrent_hash: str, original: str) -> dict[str, Any]:
        return self._submit_parsed(torrent_hash, lambda save_path: self.qb.add(original, save_path), "链接")

    def submit(self, magnet: str) -> dict[str, Any]:
        torrent_hash, original = parse_magnet(magnet)
        with self._submission_lock():
            return self._submit_magnet_parsed(torrent_hash, original)

    def _submit_torrent_parsed(self, torrent: bytes, torrent_hash: str) -> dict[str, Any]:
        """Admit a bounded local .torrent file using its exact v1 info hash."""
        add_torrent = getattr(self.qb, "add_torrent", None)
        if not callable(add_torrent):
            raise MusicDownloadError(503, "公共下载器暂不支持 torrent 文件上传", "torrent_upload_unavailable")
        return self._submit_parsed(torrent_hash, lambda save_path: add_torrent(torrent, save_path), "torrent 文件")

    def submit_torrent(self, torrent: bytes) -> dict[str, Any]:
        torrent_hash = _torrent_v1_hash(torrent)
        with self._submission_lock():
            return self._submit_torrent_parsed(torrent, torrent_hash)

    def _reconcile_pending(self, tasks: dict[str, Any], rows: list[dict[str, Any]]) -> None:
        for torrent_hash, task in list(tasks.items()):
            if not isinstance(task, dict) or self._task_state(task) != "pending":
                continue
            row = next((item for item in rows if torrent_hash in _row_hashes(item)), None)
            if row is None:
                continue
            if str(row.get("save_path", "")) == str(task.get("save_path", "")) and _tagged(row.get("tags")):
                self._admit(torrent_hash)

    def status(self) -> dict[str, Any]:
        ledger = self._ledger_snapshot()
        tasks = {key: value for key, value in ledger.get("tasks", {}).items()
                 if isinstance(value, dict) and self._task_state(value) in {"pending", "admitted", "imported"}}
        tasks = dict(list(tasks.items())[:MAX_STATUS_TASKS])
        try:
            rows = self.qb.info()
            self._reconcile_pending(tasks, rows)
            # Refresh after reconciliation to expose the new import state.
            ledger = self._ledger_snapshot()
            answer: list[dict[str, Any]] = []
            scan_pending = False
            safe_save_path = ""
            for torrent_hash, task in list(ledger.get("tasks", {}).items())[:MAX_STATUS_TASKS]:
                if not isinstance(task, dict) or self._task_state(task) not in {"pending", "admitted", "imported"}:
                    continue
                row = next((item for item in rows if torrent_hash in _row_hashes(item)), None)
                entry: dict[str, Any] = {
                    "hash": torrent_hash,
                    "state": self._task_state(task),
                    "savepath": task.get("save_path", ""),
                    "import": _copy_json(task.get("import", {"state": "waiting", "scan_pending": False})),
                }
                if row is None:
                    entry["qbit"] = {"present": False}
                else:
                    entry["qbit"] = {"present": True, "progress": row.get("progress", 0),
                                      "state": row.get("state", ""), "error": row.get("error", "")}
                imported = entry["import"] if isinstance(entry["import"], dict) else {}
                import_state = imported.get("state", "waiting")
                entry["name"] = row.get("name", "") if row else ""
                entry["progress"] = row.get("progress", 0) if row else 0
                entry["state"] = row.get("state", entry["state"]) if row else entry["state"]
                entry["import_state"] = import_state
                entry["message"] = imported.get("message", "")
                entry["imported_files"] = len(imported.get("files", [])) if isinstance(imported.get("files"), list) else 0
                safe_save_path = safe_save_path or str(task.get("save_path", ""))
                scan_pending = scan_pending or bool(imported.get("scan_pending"))
                answer.append(entry)
            return {"tasks": answer, "truncated": len(ledger.get("tasks", {})) > MAX_STATUS_TASKS,
                    "save_path": safe_save_path, "library_path": str(self.library_path),
                    "scan_pending": scan_pending}
        except Exception as exc:
            error = _safe_error(exc)
            return {"tasks": [], "truncated": False, "save_path": "",
                    "library_path": str(self.library_path), "scan_pending": False,
                    "error": {"code": error.code, "message": error.message}}

    def _ensure_dir_no_symlink(self, path: Path) -> None:
        # Host systems may expose /tmp or /var through a symlink.  Check the
        # configured path and every component we create, while leaving those
        # system ancestors alone.
        path = path.absolute()
        missing: list[Path] = []
        current = path
        while not current.exists():
            missing.append(current)
            parent = current.parent
            if parent == current:
                break
            current = parent
        if current.is_symlink():
            raise MusicDownloadError(409, "音乐库路径包含符号链接", "library_symlink")
        if not current.is_dir():
            raise MusicDownloadError(409, "音乐库路径不是目录", "library_path_invalid")
        for candidate in reversed(missing):
            try:
                candidate.mkdir()
                if candidate.is_symlink() or not candidate.is_dir():
                    raise MusicDownloadError(409, "音乐库路径包含符号链接", "library_symlink")
            except FileExistsError:
                if candidate.is_symlink():
                    raise MusicDownloadError(409, "音乐库路径包含符号链接", "library_symlink")
                if not candidate.is_dir():
                    raise MusicDownloadError(409, "音乐库路径不是目录", "library_path_invalid")

    def _safe_source(self, name: Any) -> tuple[Path, str]:
        if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
            raise MusicDownloadError(409, "下载器文件路径无效", "unsafe_source_path")
        root = self.download_root.absolute()
        try:
            if root.is_symlink():
                raise MusicDownloadError(409, "下载器音乐目录包含符号链接", "unsafe_source_path")
        except OSError as exc:
            raise MusicDownloadError(409, "无法检查下载器音乐目录", "unsafe_source_path") from exc
        raw = name
        prefix = self.download_qb_path.rstrip("/")
        if raw.startswith("/"):
            if raw == prefix:
                relative = ""
            elif raw.startswith(prefix + "/"):
                relative = raw[len(prefix) + 1:]
            else:
                raise MusicDownloadError(409, "下载器文件路径不在音乐目录", "unsafe_source_path")
        else:
            relative = raw
        parts = PurePosixPath(relative).parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise MusicDownloadError(409, "下载器文件路径无效", "unsafe_source_path")
        candidate = root.joinpath(*parts)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise MusicDownloadError(409, "下载器文件路径不在音乐目录", "unsafe_source_path") from exc
        current = root
        for part in parts:
            current = current / part
            try:
                if current.is_symlink():
                    raise MusicDownloadError(409, "下载器文件路径包含符号链接", "unsafe_source_path")
            except OSError as exc:
                raise MusicDownloadError(409, "无法检查下载器文件路径", "unsafe_source_path") from exc
        return candidate, "/".join(parts)

    @staticmethod
    def _title(value: Any, torrent_hash: str) -> str:
        raw = str(value or "music").replace("/", "_").replace("\\", "_")
        raw = "".join(char if ord(char) >= 0x20 and ord(char) != 0x7F else "_" for char in raw)
        raw = re.sub(r"[<>:\"|?*]", "_", raw).strip(" .") or "music"
        return (raw[:70].rstrip(" .") or "music") + "-" + torrent_hash[:12]

    def _import_base(self) -> Path:
        self._ensure_dir_no_symlink(self.library_path)
        base = self.library_path / "磁力下载"
        self._ensure_dir_no_symlink(base)
        return base

    def _marker(self, path: Path) -> dict[str, Any] | None:
        marker = path / SCAN_MARKER
        try:
            if marker.is_symlink():
                return None
            value = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _own_marker(self, path: Path, torrent_hash: str, verify_hash: bool = False) -> dict[str, Any] | None:
        marker = self._marker(path)
        if (_normal_hash(marker.get("hash")) if marker else None) != torrent_hash or marker.get("version") != 1:
            return None
        files = marker.get("files")
        if not isinstance(files, list) or not files:
            return None
        for item in files:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                return None
            relative = item["path"]
            if ("\\" in relative or not relative or PurePosixPath(relative).is_absolute()
                    or any(part in {"", ".", ".."} for part in PurePosixPath(relative).parts)):
                return None
            target = path
            for part in PurePosixPath(relative).parts:
                target = target / part
                if target.is_symlink():
                    return None
            if not target.is_file():
                return None
            try:
                if int(item.get("size", 0)) != target.stat().st_size or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", ""))):
                    return None
                if verify_hash:
                    digest = hashlib.sha256()
                    with target.open("rb") as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(chunk)
                    if digest.hexdigest() != item["sha256"]:
                        return None
            except (OSError, TypeError, ValueError):
                return None
        return marker

    def _source_entries(self, row: dict[str, Any], torrent_hash: str) -> list[dict[str, Any]]:
        files = self.qb.files(torrent_hash)
        selected: list[dict[str, Any]] = []
        for item in files:
            try:
                priority = int(item.get("priority", 0) or 0)
                progress = float(item.get("progress", 0) or 0)
                declared = int(item.get("size", 0) or 0)
            except (TypeError, ValueError):
                continue
            name = item.get("name")
            if priority <= 0 or declared <= 0 or not isinstance(name, str):
                continue
            source, relative = self._safe_source(name)
            if source.suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            if progress < 1:
                raise MusicDownloadError(409, "音频文件尚未下载完成", "source_unstable")
            try:
                stat = source.stat()
            except OSError as exc:
                raise MusicDownloadError(409, "音乐源文件暂时不可读", "source_unavailable") from exc
            if not source.is_file() or stat.st_size != declared:
                raise MusicDownloadError(409, "音乐源文件尚未稳定完成", "source_unstable")
            selected.append({"source": source, "relative": relative, "size": declared,
                             "mtime_ns": stat.st_mtime_ns, "stat": stat})
        return selected

    def _recover_stage(self, base: Path, torrent_hash: str, destination: Path) -> bool:
        if os.path.lexists(destination):
            return False
        prefix = ".native-music-{}-".format(torrent_hash[:12])
        staging = base / STAGING_DIR
        self._ensure_dir_no_symlink(staging)
        for candidate in sorted(staging.glob(prefix + "*")):
            if not candidate.is_dir() or candidate.is_symlink():
                continue
            marker = self._own_marker(candidate, torrent_hash, verify_hash=True)
            if marker:
                try:
                    os.replace(candidate, destination)
                    return True
                except OSError:
                    continue
        return False

    def _copy_stable(self, torrent_hash: str, row: dict[str, Any], selected: list[dict[str, Any]],
                     base: Path, destination: Path) -> dict[str, Any]:
        total = sum(int(item["size"]) for item in selected)
        if len(selected) > MAX_IMPORT_FILES or total > MAX_IMPORT_BYTES:
            raise MusicDownloadError(409, "音乐任务超过单次导入上限，请稍后重试", "import_limit")
        usage = shutil.disk_usage(base)
        if usage.free < total + 64 * 1024**2:
            raise MusicDownloadError(409, "音乐库可用空间不足", "library_space")
        if os.path.lexists(destination):
            if destination.is_symlink():
                raise MusicDownloadError(409, "音乐库目标包含符号链接", "library_symlink")
            marker = self._own_marker(destination, torrent_hash) if destination.is_dir() else None
            if marker:
                return marker
            raise MusicDownloadError(409, "音乐库目标已存在，已保留原文件", "library_collision")
        staging = base / STAGING_DIR
        self._ensure_dir_no_symlink(staging)
        stage = Path(tempfile.mkdtemp(prefix=".native-music-{}-".format(torrent_hash[:12]), dir=str(staging)))
        try:
            manifest: list[dict[str, Any]] = []
            for item in selected:
                source: Path = item["source"]
                relative = item["relative"]
                target = stage / relative
                self._ensure_dir_no_symlink(target.parent)
                before = source.stat()
                digest = hashlib.sha256()
                with source.open("rb") as src, target.open("xb") as dst:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                        dst.write(chunk)
                    dst.flush()
                    os.fsync(dst.fileno())
                after = source.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or after.st_size != item["size"]:
                    raise MusicDownloadError(409, "音乐源文件在导入期间发生变化", "source_unstable")
                # Songloft skips files modified in the last ten seconds.
                # This complete, verified copy retains the stable source mtime
                # so the scan immediately after atomic publication can import it.
                os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
                manifest.append({"path": relative, "size": after.st_size, "sha256": digest.hexdigest()})
            marker = {"version": 1, "hash": torrent_hash, "files": manifest}
            temporary = stage / (SCAN_MARKER + ".tmp")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(marker, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, stage / SCAN_MARKER)
            os.replace(stage, destination)
            return marker
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise

    def run_once(self, scan_callback: Callable[[], Any] | None = None) -> dict[str, Any]:
        with self._run_lock():
            return self._run_once_locked(scan_callback)

    def _run_once_locked(self, scan_callback: Callable[[], Any] | None = None) -> dict[str, Any]:
        ledger = self._ledger_snapshot()
        result: dict[str, Any] = {"imported": [], "waiting": [], "errors": [], "scan_pending": []}
        run_files = 0
        run_bytes = 0
        try:
            rows = self.qb.info()
        except Exception as exc:
            error = _safe_error(exc)
            result["errors"].append({"code": error.code, "message": error.message})
            return result
        # A successful qB add can become visible just after the request
        # response. Reconcile pending admissions before taking the import
        # snapshot so one timer pass can safely begin the next phase.
        self._reconcile_pending(ledger.get("tasks", {}), rows)
        ledger = self._ledger_snapshot()
        admitted = [(key, value) for key, value in ledger.get("tasks", {}).items()
                    if isinstance(value, dict) and self._task_state(value) in {"admitted", "imported"}]
        admitted = admitted[:MAX_STATUS_TASKS]
        try:
            base = self._import_base()
        except Exception as exc:
            error = _safe_error(exc)
            result["errors"].append({"code": error.code, "message": error.message})
            return result
        for torrent_hash, task in admitted:
            row = next((item for item in rows if torrent_hash in _row_hashes(item)), None)
            if row is None:
                result["errors"].append({"hash": torrent_hash, "code": "task_missing", "message": "下载任务不存在"})
                self._mark_import(torrent_hash, {"state": "error", "message": "下载任务不存在"})
                continue
            if (str(row.get("save_path", "")) != str(task.get("save_path", "")) or
                    not _tagged(row.get("tags"))):
                message = "下载任务未保留音乐入口标记或保存目录"
                result["errors"].append({"hash": torrent_hash, "code": "admission_conflict", "message": message})
                self._mark_import(torrent_hash, {"state": "error", "message": message})
                continue
            if not _state_complete(row):
                result["waiting"].append(torrent_hash)
                self._mark_import(torrent_hash, {"state": "pending", "message": "等待下载完成"})
                continue
            destination = base / self._title(row.get("name"), torrent_hash)
            try:
                if self._recover_stage(base, torrent_hash, destination):
                    marker = self._own_marker(destination, torrent_hash)
                    if marker:
                        self._mark_import(torrent_hash, {"state": "imported", "scan_pending": True,
                                                         "files": marker.get("files", [])})
                        result["imported"].append(torrent_hash)
                        continue
                if os.path.lexists(destination):
                    if destination.is_symlink():
                        raise MusicDownloadError(409, "音乐库目标包含符号链接", "library_symlink")
                    marker = self._own_marker(destination, torrent_hash) if destination.is_dir() else None
                    if marker:
                        current_import = task.get("import", {}) if isinstance(task.get("import"), dict) else {}
                        if current_import.get("state") != "imported":
                            self._mark_import(torrent_hash, {"state": "imported", "scan_pending": True,
                                                             "files": marker.get("files", [])})
                        continue
                    else:
                        raise MusicDownloadError(409, "音乐库目标已存在，已保留原文件", "library_collision")
                selected = self._source_entries(row, torrent_hash)
                if not selected:
                    raise MusicDownloadError(409, "任务没有可导入的音频文件", "no_audio_files")
                signatures = {item["relative"]: {"size": item["size"], "mtime_ns": item["mtime_ns"]} for item in selected}
                old_stable = task.get("stable", {}) if isinstance(task.get("stable"), dict) else {}
                if old_stable != signatures:
                    self._ledger_update(lambda value, h=torrent_hash, sig=signatures:
                                        value["tasks"][h].update({"stable": sig}))
                    self._mark_import(torrent_hash, {"state": "stabilizing", "message": "等待文件稳定"})
                    result["waiting"].append(torrent_hash)
                    continue
                selected_bytes = sum(int(item["size"]) for item in selected)
                if (run_files + len(selected) > MAX_IMPORT_FILES or
                        run_bytes + selected_bytes > MAX_IMPORT_BYTES):
                    raise MusicDownloadError(409, "本次音乐导入超过上限，请稍后重试", "import_limit")
                marker = self._copy_stable(torrent_hash, row, selected, base, destination)
                run_files += len(selected)
                run_bytes += selected_bytes
                self._mark_import(torrent_hash, {"state": "imported", "scan_pending": True,
                                                 "files": marker.get("files", [])})
                result["imported"].append(torrent_hash)
            except Exception as exc:
                error = _safe_error(exc)
                if error.code == "source_unstable":
                    self._mark_import(torrent_hash, {"state": "stabilizing", "message": error.message})
                    result["waiting"].append(torrent_hash)
                else:
                    self._mark_import(torrent_hash, {"state": "error", "message": error.message})
                    result["errors"].append({"hash": torrent_hash, "code": error.code, "message": error.message})
        ledger = self._ledger_snapshot()
        pending = [key for key, value in ledger.get("tasks", {}).items()
                   if isinstance(value, dict) and isinstance(value.get("import"), dict)
                   and value["import"].get("scan_pending")]
        if pending and scan_callback is not None:
            try:
                if scan_callback():
                    self._ledger_update(lambda value: [value["tasks"][key].setdefault("import", {}).update({"scan_pending": False})
                                                        for key in pending if key in value["tasks"]])
                    pending = []
            except Exception:
                pass
        result["scan_pending"] = pending[:MAX_STATUS_TASKS]
        return result


__all__ = ["MusicDownloads", "MusicDownloadError", "QbClient", "parse_magnet"]


if __name__ == "__main__":  # pragma: no cover - timer wrapper is owned by the CGI integration
    raise SystemExit("Use MusicDownloads from the owner-gated timer")
