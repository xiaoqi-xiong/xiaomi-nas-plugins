"""Shared, non-secret configuration for the Xiaomi NAS plugin bundle."""

import json
from pathlib import Path


DEFAULTS = {
    "owner": "CHANGE_ME",
    "owner_uid": 1000,
    "nas_host": "127.0.0.1",
    "proxy_controller_host": "127.0.0.1",
    "ports": {
        "moviepilot": 3000,
        "pt": 18080,
        "public": 18081,
        "pansou": 8889,
        "proxy_controller": 19090,
    },
    "plugin_namespace": "CHANGE_ME",
}


def get_settings(app_root: Path) -> dict:
    """Load local settings, failing closed when the installer has not configured it."""
    path = app_root / "settings.json"
    if not path.is_file():
        return DEFAULTS.copy()
    data = json.loads(path.read_text(encoding="utf-8"))
    result = DEFAULTS.copy()
    result.update({key: value for key, value in data.items() if key != "ports"})
    result["ports"] = {**DEFAULTS["ports"], **data.get("ports", {})}
    return result


def owner_is_valid(settings: dict, username: str, uid: int) -> bool:
    return (settings.get("owner") not in (None, "", "CHANGE_ME")
            and username == settings["owner"]
            and uid == int(settings["owner_uid"]))
