#!/usr/bin/env python3
"""Build a source bundle from a strict public-file allowlist; never installs it."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = ("assets", "mediastack", "mteam", "xproxy", "resource-monitor", "music", "tests")
EXTENSIONS = {".py", ".cgi", ".html", ".css", ".js", ".svg", ".png"}
TOP_FILES = ("README.md", "LICENSE", "SECURITY.md", "PLUGIN-ARCHITECTURE.md", "settings.py", "settings.example.json",
             "deployment/plugins.json", "deployment/songloft.compose.yml", "deployment/songloft.env.example",
             "docs/MUSIC.md", "docs/INSTALL.md", "docs/REFERENCE-DESIGN.md",
             "scripts/build_bundle.py", "scripts/preview_suite.py")


def public_files(root):
    paths = set()
    for name in TOP_FILES:
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise ValueError("Missing or linked public file: " + name)
        paths.add(path)
    for directory in DIRECTORIES:
        for path in (root / directory).rglob("*"):
            relative = path.relative_to(root)
            if any(part.startswith(".") or part in {"private", "__pycache__", "node_modules", "vuetorrent"} for part in relative.parts):
                continue
            if path.is_file() and not path.is_symlink() and path.suffix in EXTENSIONS:
                paths.add(path)
    return sorted(paths)


def build(root, output):
    catalogue = json.loads((root / "deployment/plugins.json").read_text())
    ids = [item["id"] for item in catalogue["entries"] + catalogue["legacy_aliases"]]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate plugin ID")
    files = public_files(root)
    names = {p.relative_to(root).as_posix() for p in files}
    for entry in catalogue["entries"]:
        if entry["handler"] not in names or entry["icon"] not in names:
            raise ValueError("Missing handler/icon for " + entry["id"])
    contents = {p.relative_to(root).as_posix(): p.read_bytes() for p in files}
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}
    contents["SHA256SUMS.json"] = (json.dumps(hashes, indent=2, sort_keys=True) + "\n").encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation avoids replacing a user's previous release artifact.
    with output.open("xb") as stream, tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for name, data in sorted(contents.items()):
            info = tarfile.TarInfo("xiaomi-nas-plugins/" + name)
            info.size = len(data)
            info.mode = 0o755 if name.endswith(".cgi") else 0o644
            info.mtime = 0
            archive.addfile(info, io.BytesIO(data))
    return len(files)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    count = build(ROOT, args.output)
    print(f"Built {count} public files: {args.output}")
    print("Not installed. Runtime settings and private state are deliberately excluded.")
