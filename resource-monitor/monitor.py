"""Read-only Linux resource snapshot collector; standard library only.

The collector intentionally deals in kernel counters and reports ``None`` when
there is not enough trustworthy information to calculate a rate.  It never
changes host, Docker, or service state.
"""

from __future__ import annotations

import fcntl
import glob
import json
import math
import os
import platform
import re
import subprocess
import time
from pathlib import Path
from typing import Any


_MAX_DOCKER_OUTPUT = 2 * 1024 * 1024
_MAX_DOCKER_ROWS = 200
_STALE_AFTER = 30.0
OS_RELEASE_PATH = Path("/etc/os-release")
_MOUNT_FILESYSTEMS = {
    "9p", "btrfs", "cfs", "cifs", "exfat", "ext2", "ext3", "ext4",
    "f2fs", "fuseblk", "hfs", "hfsplus", "jfs", "nfs", "nfs4", "ntfs",
    "ntfs3", "reiserfs", "smb3", "ubifs", "udf", "ufs", "vfat", "xfs",
    "zfs",
}
_PSEUDO_FILESYSTEMS = {
    "autofs", "cgroup", "cgroup2", "configfs", "debugfs", "devpts",
    "devtmpfs", "fusectl", "hugetlbfs", "mqueue", "overlay", "proc",
    "pstore", "ramfs", "securityfs", "squashfs", "sysfs", "tmpfs",
    "tracefs",
}


def _read(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeError):
        return default


def _nonnegative_int(value: Any) -> int | None:
    try:
        number = int(str(value), 10)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number >= 0 else None


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _clean_system_value(value: Any, limit: int = 160) -> str | None:
    """Normalize a public identification value without exposing host data."""
    if value is None:
        return None
    value = str(value).replace("\x00", " ")
    value = re.sub(r"\s+", " ", value).strip()
    if not value:
        return None
    return value[:limit]


def _parse_os_release(path: Path) -> dict[str, str]:
    """Parse simple KEY=VALUE os-release lines; never source or execute them."""
    result: dict[str, str] = {}
    for line in _read(path).splitlines():
        match = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line.strip())
        if not match:
            continue
        key, value = match.groups()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        result[key] = value
    return result


def _cpu_model(proc_root: Path, uname: Any) -> str | None:
    """Read only model-like CPU keys; ignore serial and other private fields."""
    allowed = {"model name", "hardware", "processor"}
    values: dict[str, str] = {}
    for line in _read(proc_root / "cpuinfo").splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip().lower() in allowed:
            cleaned = _clean_system_value(value)
            # ``Processor : 0`` is a CPU index on many ARM procfs images,
            # not a model string.
            if key.strip().lower() == "processor" and cleaned and re.fullmatch(r"\d+", cleaned):
                continue
            if cleaned and key.strip().lower() not in values:
                values[key.strip().lower()] = cleaned
    for key in ("model name", "hardware", "processor"):
        if values.get(key):
            return values[key]
    fallback = _clean_system_value(getattr(uname, "processor", None) or platform.processor())
    return None if fallback and re.fullmatch(r"\d+", fallback) else fallback


def _system_info(proc_root: Path, sys_root: Path) -> dict[str, str | None]:
    """Return safe host identity metadata from fixed, read-only kernel paths."""
    try:
        uname = platform.uname()
    except Exception:
        uname = type("Uname", (), {})()
    model = None
    # Device-tree model is the strongest device identity on ARM NAS systems;
    # DMI product_name is a safe fallback on x86 and some embedded boards.
    for path in (
        sys_root / "firmware/devicetree/base/model",
        proc_root / "device-tree/model",
        sys_root / "class/dmi/id/product_name",
    ):
        model = _clean_system_value(_read(path))
        if model:
            break

    firmware_version = None
    # VERSION_ID is an OS release, not firmware.  Only an explicitly named
    # device-tree firmware-version property is semantic evidence of firmware.
    for path in (
        sys_root / "firmware/devicetree/base/firmware-version",
        sys_root / "firmware/devicetree/base/xiaomi,firmware-version",
        proc_root / "device-tree/firmware-version",
    ):
        firmware_version = _clean_system_value(_read(path), 80)
        if firmware_version:
            break
    release = _parse_os_release(OS_RELEASE_PATH)
    os_name = _clean_system_value(release.get("PRETTY_NAME") or release.get("NAME"))
    os_version = _clean_system_value(release.get("VERSION_ID"), 80)
    architecture = _clean_system_value(getattr(uname, "machine", None) or platform.machine(), 80)
    kernel = _clean_system_value(getattr(uname, "release", None) or platform.release(), 120)
    return {
        "model": model,
        "cpu_model": _cpu_model(proc_root, uname),
        "architecture": architecture,
        "kernel": kernel,
        "os_name": os_name,
        "os_version": os_version,
        "firmware_version": firmware_version,
    }


def _cpu_ticks(proc: Path) -> tuple[int, int]:
    """Return aggregate CPU and idle ticks, excluding guest double counting."""
    first = next((line for line in _read(proc / "stat").splitlines()
                  if line.startswith("cpu ")), "")
    fields = first.split()[1:]
    # user,nice,system,idle,iowait,irq,softirq,steal are the non-guest
    # aggregate counters.  guest and guest_nice are already included in user
    # and nice and must not be added a second time.
    values: list[int] = []
    for raw in fields[:8]:
        value = _nonnegative_int(raw)
        if value is None:
            return 0, 0
        values.append(value)
    if len(values) < 4:
        return 0, 0
    return sum(values), values[3] + (values[4] if len(values) > 4 else 0)


def _memory(proc: Path) -> dict[str, int | float | None]:
    values: dict[str, int] = {}
    for line in _read(proc / "meminfo").splitlines():
        parts = line.split()
        if len(parts) < 2 or not parts[0].endswith(":"):
            continue
        value = _nonnegative_int(parts[1])
        if value is None:
            continue
        multiplier = {"kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3}.get(
            parts[2].lower() if len(parts) > 2 else "kb", 1)
        values[parts[0][:-1]] = value * multiplier

    total = values.get("MemTotal")
    free = values.get("MemFree")
    buffers = values.get("Buffers")
    cached = values.get("Cached")
    available = values.get("MemAvailable")
    if total is not None:
        total = max(0, total)
        if available is None and free is not None:
            available = free + (buffers or 0) + (cached or 0)
        if available is not None:
            available = min(total, max(0, available))
        used = max(0, total - available) if available is not None else None
        percent = (round(100 * used / total, 1)
                   if used is not None and total > 0 else None)
    else:
        # Keep independently reliable fields when a truncated or unusual
        # meminfo omits MemTotal; usage and percentage remain unknown.
        used = percent = None

    swap_total = values.get("SwapTotal")
    swap_free = values.get("SwapFree")
    if swap_total is not None:
        swap_total = max(0, swap_total)
        swap_used = (max(0, swap_total - swap_free)
                     if swap_free is not None else None)
    else:
        swap_total = swap_used = None
    return {
        "total": total,
        "available": available,
        "used": used,
        "percent": percent,
        "free": free,
        "buffers": buffers,
        "cached": cached,
        "swap_total": swap_total,
        "swap_used": swap_used,
    }


def _network(proc: Path) -> list[dict[str, int]]:
    result: list[dict[str, int]] = []
    hidden_prefixes = ("docker", "veth", "br-", "virbr", "cni", "flannel")
    for line in _read(proc / "net/dev").splitlines()[2:]:
        if ":" not in line:
            continue
        name, raw = line.split(":", 1)
        name = name.strip()
        if not name or name == "lo" or name.startswith(hidden_prefixes):
            continue
        fields = raw.split()
        if len(fields) < 9:
            continue
        rx = _nonnegative_int(fields[0])
        tx = _nonnegative_int(fields[8])
        if rx is None or tx is None:
            continue
        result.append({"name": name, "rx": rx, "tx": tx})
    return result


def _disk_io(proc: Path) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for line in _read(proc / "diskstats").splitlines():
        fields = line.split()
        if len(fields) < 10:
            continue
        name = fields[2]
        if name.startswith(("loop", "ram", "zram", "dm-", "md")):
            continue
        # A partition has the same counter family as its parent.  Counting
        # both would inflate host I/O, so retain whole devices only.
        if (re.fullmatch(r"(?:sd|hd|vd|xvd)[a-z]+\d+", name)
                or re.fullmatch(r"nvme\d+n\d+p\d+", name)
                or re.fullmatch(r"mmcblk\d+p\d+", name)):
            continue
        reads = _nonnegative_int(fields[5])
        writes = _nonnegative_int(fields[9])
        if reads is None or writes is None:
            continue
        result[name] = {"read": reads * 512, "write": writes * 512}
    return result


def _decode_mount_field(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)


def _mounts(proc: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen_sources: set[str] = set()
    for line in _read(proc / "mounts").splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        source, mountpoint, fs_type = (_decode_mount_field(item) for item in fields[:3])
        fs_type = fs_type.lower()
        if fs_type in _PSEUDO_FILESYSTEMS or fs_type not in _MOUNT_FILESYSTEMS:
            continue
        if not source.startswith("/dev/") and fs_type not in {"cfs", "cifs", "nfs", "nfs4", "9p", "smb3"}:
            continue
        # The same block/network volume can appear through several bind or
        # namespace mounts.  Keep one row so consumers never sum it twice.
        source_key = f"{fs_type}:{source}"
        if source_key in seen_sources:
            continue
        seen_sources.add(source_key)
        total: int | None = None
        free: int | None = None
        try:
            stat = os.statvfs(mountpoint)
            total = max(0, int(stat.f_blocks) * int(stat.f_frsize))
            free = max(0, int(stat.f_bavail) * int(stat.f_frsize))
            free = min(total, free)
        except (OSError, ValueError, OverflowError):
            pass
        used = (max(0, total - free) if total is not None and free is not None else None)
        used_percent = (round(100 * used / total, 1)
                        if used is not None and total else None)
        result.append({"source": source, "mount": mountpoint, "fs": fs_type,
                       "total": total, "free": free, "used_percent": used_percent})
    return result


def _temperatures(sys_root: Path) -> list[dict[str, str | float]]:
    result: list[dict[str, str | float]] = []
    paths = glob.glob(str(sys_root / "class/thermal/thermal_zone*/temp"))
    paths += glob.glob(str(sys_root / "class/hwmon/hwmon*/temp*_input"))
    for path in paths:
        sensor = Path(path)
        raw = _read(sensor).strip()
        value = _finite_number(raw)
        if value is None:
            continue
        if abs(value) > 1000:
            value /= 1000
        if not -20 <= value <= 150:
            continue
        parent = sensor.parent
        if parent.name.startswith("thermal_zone"):
            label = _read(parent / "type", parent.name).strip()
        else:
            label = _read(parent / "name", parent.name).strip()
            suffix = sensor.name.removesuffix("_input")
            label = _read(parent / (suffix + "_label"), label).strip()
        result.append({"name": label or parent.name, "celsius": round(value, 1)})
    unique: dict[tuple[str, float], dict[str, str | float]] = {}
    for item in result:
        unique[(str(item["name"]), float(item["celsius"]))] = item
    return list(unique.values())[:12]


def _json_lines(raw: str) -> list[dict]:
    rows = []
    for line in raw.splitlines():
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
        except (TypeError, ValueError):
            continue
    return rows


def _docker_binary() -> str | None:
    # Xiaomi NAS images commonly install Docker at /data/docker/docker.
    candidates = ("/data/docker/docker", "/usr/bin/docker", "/usr/local/bin/docker", "/bin/docker")
    return next((candidate for candidate in candidates
                 if os.path.isfile(candidate) and os.access(candidate, os.X_OK)), None)


def _docker() -> dict[str, Any]:
    """Optional Docker CLI snapshot using only fixed, read-only commands."""
    sampled_at = int(time.time())
    base: dict[str, Any] = {
        "available": False, "sampled_at": sampled_at,
        "running": 0, "stopped": 0, "total": 0,
        "projects": [], "project_count": 0,
        "stats_available": False, "containers": [], "warnings": [],
    }
    binary = _docker_binary()
    if not binary:
        base["reason"] = "Docker 命令不可用或插件账号无权访问"
        return base

    try:
        ps = subprocess.run(
            [binary, "ps", "-a", "--format", "{{json .}}"],
            capture_output=True, text=True, timeout=3, check=False)
    except (OSError, subprocess.TimeoutExpired):
        base["reason"] = "Docker 查询超时或未运行"
        return base
    if ps.returncode:
        base["reason"] = "Docker 只读状态不可用；请检查插件运行账号权限"
        return base

    rows = _json_lines(str(ps.stdout or "")[:_MAX_DOCKER_OUTPUT])[:_MAX_DOCKER_ROWS]
    containers: list[dict[str, Any]] = []
    for row in rows:
        labels = row.get("Labels", "")
        if isinstance(labels, dict):
            project = str(labels.get("com.docker.compose.project", ""))
        else:
            project = next((part.split("=", 1)[1] for part in str(labels).split(",")
                            if part.startswith("com.docker.compose.project=") and "=" in part), "")
        containers.append({
            "id": str(row.get("ID", ""))[:16],
            "name": str(row.get("Names", ""))[:100],
            "image": str(row.get("Image", ""))[:120],
            "state": str(row.get("State", "unknown"))[:40],
            "status": str(row.get("Status", ""))[:80],
            "ports": str(row.get("Ports", ""))[:240],
            "project": project[:100],
        })

    stats: dict[str, dict[str, str]] = {}
    stats_available = False
    warnings: list[str] = []
    try:
        stats_run = subprocess.run(
            [binary, "stats", "--no-stream", "--format", "{{json .}}"],
            capture_output=True, text=True, timeout=4, check=False)
        if stats_run.returncode == 0:
            stats_available = True
            for row in _json_lines(str(stats_run.stdout or "")[:_MAX_DOCKER_OUTPUT])[:_MAX_DOCKER_ROWS]:
                name = str(row.get("Name", ""))
                if name:
                    stats[name] = {
                        "cpu": str(row.get("CPUPerc", "—"))[:16],
                        "memory": str(row.get("MemUsage", "—"))[:40],
                        "memory_percent": str(row.get("MemPerc", "—"))[:16],
                        # Docker reports these as cumulative human-readable
                        # strings.  Keep them cumulative; do not invent rates.
                        "network": str(row.get("NetIO", "—"))[:40],
                        "block_io": str(row.get("BlockIO", "—"))[:40],
                    }
        else:
            warnings.append("Docker stats 不可用")
    except (OSError, subprocess.TimeoutExpired):
        warnings.append("Docker stats 查询超时")

    for item in containers:
        item["stats"] = stats.get(item["name"], {})
    running = sum(1 for item in containers if item["state"].lower() == "running")
    project_rows: dict[str, dict[str, int | str]] = {}
    for item in containers:
        name = item["project"] or ""
        group = project_rows.setdefault(name, {"name": name, "total": 0, "running": 0, "stopped": 0})
        group["total"] += 1
        if item["state"].lower() == "running":
            group["running"] += 1
        else:
            group["stopped"] += 1
    base.update({"available": True, "running": running,
                 "stopped": len(containers) - running, "total": len(containers),
                 "projects": list(project_rows.values()),
                 "project_count": len(project_rows), "stats_available": stats_available,
                 "warnings": warnings, "containers": containers})
    return base


def _state_read(state_file: Path) -> dict[str, Any]:
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _state_write(state_file: Path, data: dict[str, Any]) -> None:
    temporary = state_file.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, separators=(",", ":"))
        os.chmod(temporary, 0o600)
        os.replace(temporary, state_file)
        os.chmod(state_file, 0o600)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _counter_is_monotonic(current: dict[str, Any], previous: dict[str, Any]) -> bool:
    for current_key, previous_key in (("cpu_total", "cpu_total"), ("cpu_idle", "cpu_idle")):
        new = _nonnegative_int(current.get(current_key))
        old = _nonnegative_int(previous.get(previous_key))
        if new is None or old is None or new < old:
            return False
    old_net = {str(item.get("name")): item for item in previous.get("network", [])
               if isinstance(item, dict)}
    for item in current.get("network", []):
        old = old_net.get(str(item.get("name")))
        if old is None:
            continue
        for key in ("rx", "tx"):
            new = _nonnegative_int(item.get(key)); prior = _nonnegative_int(old.get(key))
            if new is None or prior is None or new < prior:
                return False
    old_disks = previous.get("disk_io", {})
    for name, item in current.get("disk_io", {}).items():
        old = old_disks.get(name)
        if old is None:
            continue
        for key in ("read", "write"):
            new = _nonnegative_int(item.get(key)); prior = _nonnegative_int(old.get(key))
            if new is None or prior is None or new < prior:
                return False
    return True


def _rates(current: dict[str, Any], previous: dict[str, Any], elapsed: float,
           usable: bool) -> tuple[float | None, list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if not usable or elapsed <= 0:
        network = [{**item, "rx_rate": None, "tx_rate": None}
                   for item in current["network"]]
        disk = {name: {"read_rate": None, "write_rate": None}
                for name in current["disk_io"]}
        return None, network, disk
    old_net = {str(item.get("name")): item for item in previous.get("network", [])
               if isinstance(item, dict)}
    network = []
    for item in current["network"]:
        old = old_net.get(item["name"])
        if old is None:
            rx_rate = tx_rate = None
        else:
            rx_rate = round((item["rx"] - old["rx"]) / elapsed, 3)
            tx_rate = round((item["tx"] - old["tx"]) / elapsed, 3)
        network.append({**item, "rx_rate": rx_rate, "tx_rate": tx_rate})
    old_disks = previous.get("disk_io", {})
    disk = {}
    for name, item in current["disk_io"].items():
        old = old_disks.get(name)
        if old is None:
            disk[name] = {"read_rate": None, "write_rate": None}
        else:
            disk[name] = {"read_rate": round((item["read"] - old["read"]) / elapsed, 3),
                          "write_rate": round((item["write"] - old["write"]) / elapsed, 3)}
    delta_total = current["cpu_total"] - previous["cpu_total"]
    delta_idle = current["cpu_idle"] - previous["cpu_idle"]
    if delta_total <= 0:
        cpu = None
    else:
        cpu = round(max(0.0, min(100.0, 100 * (delta_total - delta_idle) / delta_total)), 1)
    return cpu, network, disk


def _collect_locked(proc_root: Path, sys_root: Path, state_file: Path | None,
                    previous: dict[str, Any], now: float,
                    monotonic_now: float | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    monotonic_now = _finite_number(monotonic_now if monotonic_now is not None else time.monotonic())
    if monotonic_now is None:
        monotonic_now = 0.0
    total_ticks, idle_ticks = _cpu_ticks(proc_root)
    uptime_raw = _read(proc_root / "uptime", "").split()
    uptime = _finite_number(uptime_raw[0]) if uptime_raw else None
    current = {
        # ``time`` is retained for old state readers and display debugging;
        # only ``monotonic`` is ever used to derive elapsed time.
        "time": now, "monotonic": monotonic_now,
        "cpu_total": total_ticks, "cpu_idle": idle_ticks,
        "network": _network(proc_root), "disk_io": _disk_io(proc_root),
        "boot_id": _read(proc_root / "sys/kernel/random/boot_id").strip(),
        "uptime": uptime,
    }
    warnings: list[str] = []
    persisted = bool(state_file)
    if not persisted:
        sample_state = "unavailable"
        usable = False
        elapsed = 0.0
    elif not previous or _finite_number(previous.get("monotonic")) is None:
        sample_state = "warming"
        usable = False
        elapsed = 0.0
    else:
        previous_monotonic = _finite_number(previous.get("monotonic"))
        elapsed = monotonic_now - previous_monotonic if previous_monotonic is not None else 0.0
        previous_boot = str(previous.get("boot_id", ""))
        current_boot = current["boot_id"]
        rebooted = bool(previous_boot and current_boot and previous_boot != current_boot)
        uptime_reset = (current["uptime"] is not None and _finite_number(previous.get("uptime")) is not None
                        and current["uptime"] < float(previous["uptime"]))
        monotonic = _counter_is_monotonic(current, previous)
        if rebooted or uptime_reset or not monotonic:
            sample_state = "reset"
            usable = False
        elif elapsed <= 0:
            sample_state = "reset"
            usable = False
        elif elapsed > _STALE_AFTER:
            sample_state = "stale"
            usable = False
        else:
            sample_state = "ok"
            usable = True
    if sample_state == "unavailable":
        warnings.append("状态采样未启用持久化")
    elif sample_state == "warming":
        warnings.append("等待下一次采样以计算速率")
    elif sample_state == "reset":
        warnings.append("检测到重启或计数器重置，已清空速率")
    elif sample_state == "stale":
        warnings.append("上次采样过旧，已清空速率")

    cpu, network, disk_io = _rates(current, previous, elapsed, usable)
    if total_ticks == 0:
        warnings.append("CPU 计数器不可用")
        cpu = None
    cores = sum(1 for line in _read(proc_root / "cpuinfo").splitlines()
                if re.match(r"^processor\s*:\s*\d+\s*$", line))
    load = _read(proc_root / "loadavg", "").split()[:3]
    if len(load) != 3:
        load = []
    status = {
        "sampled_at": int(now),
        "sample_state": sample_state,
        "warnings": warnings,
        "system": _system_info(proc_root, sys_root),
        "uptime_seconds": int(uptime) if uptime is not None else None,
        "cpu_percent": cpu,
        "cpu_cores": cores,
        "load_average": load,
        "memory": _memory(proc_root),
        "network": network,
        "disk_io": disk_io,
        "volumes": _mounts(proc_root),
        "temperatures": _temperatures(sys_root),
    }
    return status, current


def collect_status(proc_root: Path = Path("/proc"), sys_root: Path = Path("/sys"),
                   state_file: Path | None = None) -> dict[str, Any]:
    """Collect one host sample while serializing state updates when enabled."""
    proc_root = Path(proc_root)
    sys_root = Path(sys_root)
    if state_file is None:
        status, _ = _collect_locked(proc_root, sys_root, None, {}, time.time(), time.monotonic())
        return status

    state_file = Path(state_file)
    try:
        state_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(state_file.parent, 0o700)
        lock_path = state_file.with_suffix(".lock")
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.chmod(lock_path, 0o600)
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            # Lock before reading counters.  This prevents two concurrent CGI
            # requests from collecting in opposite order and inverting rates.
            previous = _state_read(state_file)
            now = time.time()
            status, current = _collect_locked(proc_root, sys_root, state_file, previous, now, time.monotonic())
            try:
                _state_write(state_file, current)
            except (OSError, ValueError, TypeError):
                status["sample_state"] = "unavailable"
                status["warnings"] = list(status.get("warnings", [])) + ["状态无法持久化"]
                for item in status.get("network", []):
                    item["rx_rate"] = item["tx_rate"] = None
                for item in status.get("disk_io", {}).values():
                    item["read_rate"] = item["write_rate"] = None
                status["cpu_percent"] = None
            return status
        finally:
            os.close(lock_fd)
    except (OSError, ValueError, TypeError):
        # Collection remains useful even if the private state directory cannot
        # be created.  It must be explicit that rate persistence is unavailable.
        try:
            status, _ = _collect_locked(proc_root, sys_root, None, {}, time.time(), time.monotonic())
        except Exception:
            return {"sampled_at": int(time.time()), "sample_state": "unavailable",
                    "warnings": ["资源采样不可用"], "cpu_percent": None,
                    "system": {key: None for key in ("model", "cpu_model", "architecture",
                                                       "kernel", "os_name", "os_version",
                                                       "firmware_version")},
                    "uptime_seconds": None, "cpu_cores": 0, "load_average": [],
                    "memory": {}, "network": [], "disk_io": {}, "volumes": [],
                    "temperatures": []}
        status["sample_state"] = "unavailable"
        status["warnings"] = list(status.get("warnings", [])) + ["状态无法持久化"]
        return status
