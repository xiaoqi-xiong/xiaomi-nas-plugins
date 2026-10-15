import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


MODULE = Path(__file__).resolve().parents[1] / "resource-monitor" / "monitor.py"
SPEC = importlib.util.spec_from_file_location("resource_monitor", MODULE)
monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor)


class ResourceMonitorTests(unittest.TestCase):
    def _fake_proc(self, directory, cpu, rx=100, tx=200, uptime="10"):
        proc = Path(directory)
        proc.mkdir(parents=True, exist_ok=True)
        (proc / "net").mkdir(exist_ok=True)
        (proc / "stat").write_text("cpu " + cpu + "\n")
        (proc / "net/dev").write_text(
            "Inter-| Receive | Transmit\n"
            " face | bytes packets errs drop fifo frame compressed multicast | "
            "bytes packets errs drop fifo colls carrier compressed\n"
            f"eth0: {rx} 0 0 0 0 0 0 0 {tx} 0 0 0 0 0 0 0\n"
        )
        (proc / "diskstats").write_text("8 0 sda 1 0 2 0 3 0 4 0 0 0 0\n")
        (proc / "uptime").write_text(f"{uptime} 0\n")
        (proc / "cpuinfo").write_text("processor : 0\n")
        (proc / "loadavg").write_text("1.00 2.00 3.00 4/5 6\n")
        (proc / "meminfo").write_text(
            "MemTotal: 100 kB\nMemAvailable: 40 kB\nMemFree: 20 kB\n"
            "Buffers: 10 kB\nCached: 10 kB\nSwapTotal: 10 kB\nSwapFree: 3 kB\n"
        )
        (proc / "mounts").write_text("")
        return proc

    def test_cpu_ticks_excludes_guest_double_count_and_rejects_malformed(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            (proc / "stat").write_text("cpu 10 20 30 40 5 6 7 8 90 100\n")
            self.assertEqual(monitor._cpu_ticks(proc), (126, 45))
            (proc / "stat").write_text("cpu 10 nope 30\n")
            self.assertEqual(monitor._cpu_ticks(proc), (0, 0))

    def test_system_info_uses_safe_identity_paths_and_keeps_os_version_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc = root / "proc"
            sys_root = root / "sys"
            (proc / "device-tree").mkdir(parents=True)
            (sys_root / "firmware/devicetree/base").mkdir(parents=True)
            (sys_root / "class/dmi/id").mkdir(parents=True)
            (proc / "cpuinfo").write_text(
                "processor : 0\nHardware : Xiaomi ARM QA\nSerial : PRIVATE\n"
            )
            (sys_root / "firmware/devicetree/base/model").write_bytes(b"Xiaomi NAS QA\x00")
            (sys_root / "firmware/devicetree/base/firmware-version").write_bytes(b"FW-QA-1.2\x00")
            release = root / "os-release"
            release.write_text('NAME="Xiaomi OS"\nVERSION_ID="9.1"\n')
            with patch.object(monitor, "OS_RELEASE_PATH", release), \
                 patch.object(monitor.platform, "uname", return_value=SimpleNamespace(
                     machine="aarch64", release="6.6.1-qa", processor="")):
                system = monitor._system_info(proc, sys_root)
            self.assertEqual(system["model"], "Xiaomi NAS QA")
            self.assertEqual(system["cpu_model"], "Xiaomi ARM QA")
            self.assertEqual(system["architecture"], "aarch64")
            self.assertEqual(system["kernel"], "6.6.1-qa")
            self.assertEqual(system["os_name"], "Xiaomi OS")
            self.assertEqual(system["os_version"], "9.1")
            self.assertEqual(system["firmware_version"], "FW-QA-1.2")
            self.assertNotIn("Serial", system)
            self.assertNotIn("hostname", system)

    def test_os_release_version_alone_is_not_reported_as_firmware(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc = root / "proc"
            sys_root = root / "sys"
            proc.mkdir()
            sys_root.mkdir()
            (proc / "cpuinfo").write_text("Processor : ARMv8 Processor\n")
            release = root / "os-release"
            release.write_text('PRETTY_NAME="QA Linux"\nVERSION_ID="42"\n')
            with patch.object(monitor, "OS_RELEASE_PATH", release), \
                 patch.object(monitor.platform, "uname", return_value=SimpleNamespace(
                     machine="arm64", release="6.1", processor="")):
                system = monitor._system_info(proc, sys_root)
            self.assertEqual(system["os_name"], "QA Linux")
            self.assertEqual(system["os_version"], "42")
            self.assertIsNone(system["firmware_version"])

    def test_state_warms_then_rates_and_resets_on_counter_decrease(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = self._fake_proc(Path(directory) / "proc", "100 0 100 100 0 0 0 0")
            sys_root = Path(directory) / "sys"
            sys_root.mkdir()
            state = Path(directory) / "private" / "resource-monitor.json"
            with patch.object(monitor.time, "time", side_effect=[100, 110, 120]), \
                 patch.object(monitor.time, "monotonic", side_effect=[10, 20, 30]):
                first = monitor.collect_status(proc, sys_root, state)
                self._fake_proc(proc, "110 0 100 105 0 0 0 0", 110, 220)
                second = monitor.collect_status(proc, sys_root, state)
                self._fake_proc(proc, "90 0 100 105 0 0 0 0", 120, 230)
                reset = monitor.collect_status(proc, sys_root, state)
            self.assertEqual(first["sample_state"], "warming")
            self.assertIsNone(first["cpu_percent"])
            self.assertEqual(second["sample_state"], "ok")
            self.assertAlmostEqual(second["network"][0]["rx_rate"], 1.0)
            self.assertEqual(reset["sample_state"], "reset")
            self.assertIsNone(reset["network"][0]["rx_rate"])
            self.assertEqual(state.stat().st_mode & 0o777, 0o600)
            self.assertEqual(state.with_suffix(".lock").stat().st_mode & 0o777, 0o600)
            self.assertEqual(state.parent.stat().st_mode & 0o777, 0o700)

    def test_wallclock_rollback_does_not_break_monotonic_rates(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = self._fake_proc(Path(directory) / "proc", "100 0 100 100 0 0 0 0")
            sys_root = Path(directory) / "sys"
            sys_root.mkdir()
            state = Path(directory) / "private" / "resource-monitor.json"
            with patch.object(monitor.time, "time", side_effect=[100, 90]), \
                 patch.object(monitor.time, "monotonic", side_effect=[1000, 1010]):
                first = monitor.collect_status(proc, sys_root, state)
                self._fake_proc(proc, "110 0 100 105 0 0 0 0", 110, 220)
                second = monitor.collect_status(proc, sys_root, state)
            self.assertEqual(first["sample_state"], "warming")
            self.assertEqual(second["sample_state"], "ok")
            self.assertAlmostEqual(second["network"][0]["rx_rate"], 1.0)
            self.assertEqual(second["sampled_at"], 90)

    def test_old_wallclock_only_state_warms_and_replaces_delta_clock(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = self._fake_proc(Path(directory) / "proc", "100 0 100 100 0 0 0 0")
            sys_root = Path(directory) / "sys"
            sys_root.mkdir()
            state = Path(directory) / "private" / "resource-monitor.json"
            state.parent.mkdir()
            state.write_text('{"time": 100, "cpu_total": 1, "cpu_idle": 1}')
            with patch.object(monitor.time, "time", return_value=200), \
                 patch.object(monitor.time, "monotonic", return_value=20):
                result = monitor.collect_status(proc, sys_root, state)
            self.assertEqual(result["sample_state"], "warming")
            self.assertIsNone(result["cpu_percent"])
            self.assertIn("monotonic", json.loads(state.read_text()))

    def test_mounts_decode_escapes_include_cfs_and_dedupe_source(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            mount = Path(directory) / "mounted path"
            mount.mkdir()
            encoded_mount = str(mount).replace(" ", "\\040")
            (proc / "mounts").write_text(
                "nas:/share " + encoded_mount +
                " cfs rw 0 0\n"
                "nas:/share /second cfs rw 0 0\n"
                "/dev/sda1 /ignored pseudo rw 0 0\n"
            )
            stat = os.statvfs(mount)
            rows = monitor._mounts(proc)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["fs"], "cfs")
            self.assertEqual(rows[0]["mount"], str(mount))
            self.assertEqual(rows[0]["total"], stat.f_blocks * stat.f_frsize)

    def test_json_lines_discards_invalid_rows(self):
        rows = monitor._json_lines('{"Names":"mp"}\nnot-json\n{"Names":"qb"}\n')
        self.assertEqual([row["Names"] for row in rows], ["mp", "qb"])

    def test_disk_io_avoids_counting_partitions_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            (proc / "diskstats").write_text(
                "8 0 sda 10 0 20 0 30 0 40 0 0 0 0\n"
                "8 1 sda1 10 0 20 0 30 0 40 0 0 0 0\n"
                "259 0 nvme0n1 10 0 20 0 30 0 40 0 0 0 0\n"
                "259 1 nvme0n1p1 10 0 20 0 30 0 40 0 0 0 0\n"
            )
            rows = monitor._disk_io(proc)
        self.assertEqual(set(rows), {"sda", "nvme0n1"})

    def test_docker_unavailable_is_reported_without_error(self):
        with patch.object(monitor.os.path, "isfile", return_value=False):
            result = monitor._docker()
        self.assertFalse(result["available"])
        self.assertIn("Docker", result["reason"])

    def test_docker_uses_only_read_queries_and_summarizes_containers(self):
        def run(args, **kwargs):
            if args[1] == "ps":
                return type("Result", (), {"returncode": 0, "stdout":
                    '{"ID":"1234567890abcdef","Names":"moviepilot","Image":"mp:latest","State":"running","Status":"Up 2 hours","Labels":"com.docker.compose.project=media"}\n'
                    '{"ID":"9876543210abcdef","Names":"old","Image":"qb:latest","State":"exited","Status":"Exited","Labels":""}\n'})()
            return type("Result", (), {"returncode": 0, "stdout":
                '{"Name":"moviepilot","CPUPerc":"1.2%","MemUsage":"100MiB / 1GiB","MemPerc":"9.8%","NetIO":"1kB / 2kB","BlockIO":"3kB / 4kB"}\n'})()

        with patch.object(monitor.os.path, "isfile", return_value=True), \
             patch.object(monitor.os, "access", return_value=True), \
             patch.object(monitor.subprocess, "run", side_effect=run) as mocked:
            result = monitor._docker()
        self.assertEqual((result["running"], result["stopped"], result["total"]), (1, 1, 2))
        self.assertEqual(result["containers"][0]["project"], "media")
        self.assertEqual(result["containers"][0]["stats"]["cpu"], "1.2%")
        self.assertTrue(all(call.args[0][1] in {"ps", "stats"} for call in mocked.call_args_list))

    def test_docker_keeps_ports_projects_and_distinguishes_stats_failure(self):
        def run(args, **kwargs):
            if args[1] == "ps":
                return type("Result", (), {"returncode": 0, "stdout":
                    '{"Names":"a","State":"running","Ports":"0.0.0.0:80->80/tcp","Labels":"com.docker.compose.project=one"}\n'
                    '{"Names":"b","State":"exited","Ports":"","Labels":"com.docker.compose.project=one"}\n'})()
            return type("Result", (), {"returncode": 1, "stdout": ""})()

        with patch.object(monitor.os.path, "isfile", return_value=True), \
             patch.object(monitor.os, "access", return_value=True), \
             patch.object(monitor.subprocess, "run", side_effect=run):
            result = monitor._docker()
        self.assertTrue(result["available"])
        self.assertFalse(result["stats_available"])
        self.assertEqual(result["containers"][0]["ports"], "0.0.0.0:80->80/tcp")
        self.assertEqual(result["projects"], [{"name": "one", "total": 2, "running": 1, "stopped": 1}])


if __name__ == "__main__":
    unittest.main()
