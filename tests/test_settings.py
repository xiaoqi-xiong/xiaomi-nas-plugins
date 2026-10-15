import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE = Path(__file__).resolve().parents[1] / "settings.py"
SPEC = importlib.util.spec_from_file_location("plugin_settings", MODULE)
settings_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(settings_module)


class SettingsTests(unittest.TestCase):
    def test_defaults_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            config = settings_module.get_settings(Path(directory))
            self.assertFalse(settings_module.owner_is_valid(config, "someone", 1000))

    def test_owner_and_uid_must_both_match(self):
        config = {"owner": "nas-user", "owner_uid": 1234}
        self.assertTrue(settings_module.owner_is_valid(config, "nas-user", 1234))
        self.assertFalse(settings_module.owner_is_valid(config, "other", 1234))
        self.assertFalse(settings_module.owner_is_valid(config, "nas-user", 4321))

    def test_settings_override_defaults_without_dropping_other_ports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "settings.json").write_text(json.dumps({
                "owner": "nas-user", "owner_uid": 1234,
                "ports": {"pt": 28080}
            }), encoding="utf-8")
            config = settings_module.get_settings(root)
        self.assertEqual(config["owner"], "nas-user")
        self.assertEqual(config["ports"]["pt"], 28080)
        self.assertEqual(config["ports"]["public"], 18081)


if __name__ == "__main__":
    unittest.main()
