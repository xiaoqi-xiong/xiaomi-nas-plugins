import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bundle", ROOT / "scripts/build_bundle.py")
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


class BundleTests(unittest.TestCase):
    def test_allowlist_excludes_runtime_state_and_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in bundle.TOP_FILES:
                p = root / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("{}")
            for name in ["settings.json", "private/key.py", "mediastack/private/token.js", "mediastack/__pycache__/bad.py", "mediastack/live.log", "mediastack/ui.js"]:
                p = root / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("private fixture")
            (root / "mediastack/linked.js").symlink_to(root / "settings.json")
            names = {p.relative_to(root).as_posix() for p in bundle.public_files(root)}
            self.assertIn("mediastack/ui.js", names)
            for name in ["settings.json", "private/key.py", "mediastack/private/token.js", "mediastack/__pycache__/bad.py", "mediastack/live.log", "mediastack/linked.js"]:
                self.assertNotIn(name, names)
