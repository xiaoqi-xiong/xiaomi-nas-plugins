import json
import os
from pathlib import Path
import tempfile
import unittest

from music.torrent_import import QbClient, MusicDownloadError, MusicDownloads, _torrent_v1_hash, parse_magnet


HASH = "0123456789abcdef0123456789abcdef01234567"
MAGNET = "magnet:?xt=urn:btih:" + HASH


class FakeQb:
    def __init__(self, root: Path):
        self.root = root
        self.rows = []
        self.file_rows = {}
        self.preferences_value = {"save_path": "/media/downloads/public", "up_limit": 1048576,
                                  "proxy_type": "SOCKS5"}
        self.add_calls = []

    def info(self):
        return [dict(row) for row in self.rows]

    def preferences(self):
        return dict(self.preferences_value)

    def maindata(self):
        return {"server_state": {"free_space_on_disk": 100 * 1024**3}}

    def files(self, torrent_hash):
        return [dict(row) for row in self.file_rows.get(torrent_hash, [])]

    def add(self, magnet, save_path):
        self.add_calls.append((magnet, save_path))
        self.rows.append({"hash": HASH, "name": "Test Album", "save_path": save_path,
                          "tags": "native-music", "progress": 0, "state": "downloading",
                          "amount_left": 100})
        return "Ok."

    def add_torrent(self, torrent, save_path):
        torrent_hash = _torrent_v1_hash(torrent)
        self.add_calls.append(("torrent", torrent_hash, save_path))
        self.rows.append({"hash": torrent_hash, "name": "Test Album", "save_path": save_path,
                          "tags": "native-music", "progress": 0, "state": "metaDL",
                          "amount_left": 100})
        return {"success_count": 1, "failure_count": 0}


class MusicImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.download = self.root / "downloads"
        self.library = self.root / "library"
        self.download.mkdir()
        self.library.mkdir()
        self.qb = FakeQb(self.root)
        self.settings = {
            "nas_host": "127.0.0.1",
            "ports": {"public": 18081},
            "music": {"download_root": str(self.download),
                      "download_qb_path": "/media/downloads/public/music",
                      "library_path": str(self.library)},
        }
        self.downloads = MusicDownloads(self.settings, self.root, qb=self.qb)

    def tearDown(self):
        self.temp.cleanup()

    def test_qb_host_is_independent_from_nas_host(self):
        client = QbClient({"nas_host": "xiaomi-nas", "ports": {"public": 18081},
                           "music": {"qb_host": "download-node"}}, self.root)
        self.assertEqual(client.host, "download-node")
        fallback = QbClient({"nas_host": "xiaomi-nas", "ports": {"public": 18081}}, self.root)
        self.assertEqual(fallback.host, "xiaomi-nas")

    def test_qb_host_rejects_urls_and_credentials(self):
        with self.assertRaises(MusicDownloadError) as raised:
            QbClient({"nas_host": "xiaomi-nas", "ports": {"public": 18081},
                      "music": {"qb_host": "http://download-node"}}, self.root)
        self.assertEqual(raised.exception.code, "qb_unconfigured")

    def _add_complete(self, torrent_hash=HASH, name="Test Album"):
        self.qb.rows = [{"hash": torrent_hash, "name": name,
                         "save_path": "/media/downloads/public/music", "tags": "native-music",
                         "progress": 1, "state": "seeding", "amount_left": 0}]

    def test_magnet_validation_and_base32(self):
        with self.assertRaises(MusicDownloadError):
            parse_magnet("magnet:?xt=urn:btih:not-a-hash")
        with self.assertRaises(MusicDownloadError):
            parse_magnet("magnet:?xt=urn:btih:" + HASH + "\n")
        base32 = "a" * 32
        parsed, _ = parse_magnet("magnet:?dn=test&xt=urn:btih:" + base32)
        self.assertEqual(len(parsed), 40)

    def test_existing_baseline_is_conflict_and_preferences_are_not_changed(self):
        self._add_complete()
        before = dict(self.qb.preferences_value)
        with self.assertRaises(MusicDownloadError) as raised:
            self.downloads.submit(MAGNET)
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(self.qb.preferences_value, before)
        self.assertEqual(self.qb.add_calls, [])
        self.assertFalse((self.root / "private" / "music-downloads.json").exists())

    def test_submit_uses_fixed_tag_and_is_idempotent(self):
        answer = self.downloads.submit(MAGNET)
        self.assertEqual(answer["status"], "added")
        self.assertEqual(self.qb.add_calls, [(MAGNET, "/media/downloads/public/music")])
        ledger = json.loads((self.root / "private" / "music-downloads.json").read_text())
        self.assertEqual(ledger["tasks"][HASH]["state"], "admitted")
        self.assertNotIn("magnet", json.dumps(ledger))
        self.assertEqual(self.downloads.submit(MAGNET)["status"], "exists")
        self.assertEqual(len(self.qb.add_calls), 1)

    def test_torrent_upload_hashes_raw_info_dictionary_and_is_admitted(self):
        torrent = (b"d4:infod6:lengthi1e4:name4:test12:piece lengthi16384e6:pieces20:"
                   b"aaaaaaaaaaaaaaaaaaaaee")
        torrent_hash = _torrent_v1_hash(torrent)
        result = self.downloads.submit_torrent(torrent)
        self.assertEqual(result["hash"], torrent_hash)
        self.assertEqual(result["status"], "added")
        self.assertEqual(self.qb.add_calls[0][0], "torrent")
        ledger = json.loads((self.root / "private" / "music-downloads.json").read_text())
        self.assertEqual(ledger["tasks"][torrent_hash]["state"], "admitted")
        with self.assertRaises(MusicDownloadError):
            self.downloads.submit_torrent(b"d4:infod3:bare")

    def test_existing_music_magnet_can_receive_its_matching_metadata(self):
        torrent = (b"d4:infod6:lengthi1e4:name4:test12:piece lengthi16384e6:pieces20:"
                   b"aaaaaaaaaaaaaaaaaaaaee")
        self.downloads.submit_torrent(torrent)
        row = self.qb.rows[0]
        self.assertEqual(row['state'], 'metaDL')
        def metadata(raw, path):
            self.assertEqual(path, '/media/downloads/public/music')
            self.assertEqual(_torrent_v1_hash(raw), row['hash'])
            row['state'] = 'downloading'
            return 'Ok.'
        self.qb.add_torrent = metadata
        self.assertEqual(self.downloads.submit_torrent(torrent)['status'], 'metadata_added')
        self.assertEqual(len(self.qb.rows), 1)

    def test_partial_task_waits_then_imports_only_finished_audio_after_stable_observation(self):
        self.downloads.submit(MAGNET)
        album = self.download / "Test Album"
        album.mkdir()
        song = album / "01-song.mp3"
        song.write_bytes(b"audio")
        (album / "cover.jpg").write_bytes(b"image")
        self.qb.file_rows[HASH] = [
            {"name": "Test Album/01-song.mp3", "size": 5, "progress": 1, "priority": 1},
            {"name": "Test Album/cover.jpg", "size": 5, "progress": 1, "priority": 1},
        ]
        self.qb.rows[0]["progress"] = 0.5
        self.qb.rows[0]["state"] = "downloading"
        self.assertEqual(self.downloads.run_once()["waiting"], [HASH])
        self.qb.rows[0]["progress"] = 1
        self.qb.rows[0]["state"] = "seeding"
        self.qb.file_rows[HASH][0]["progress"] = 0.5
        self.assertEqual(self.downloads.run_once()["waiting"], [HASH])
        self.qb.file_rows[HASH][0]["progress"] = 1
        first = self.downloads.run_once(scan_callback=lambda: False)
        self.assertEqual(first["waiting"], [HASH])
        second = self.downloads.run_once(scan_callback=lambda: False)
        self.assertEqual(second["imported"], [HASH])
        self.assertEqual(second["scan_pending"], [HASH])
        destination = self.library / "磁力下载" / ("Test Album-" + HASH[:12])
        self.assertEqual((destination / "Test Album" / "01-song.mp3").read_bytes(), b"audio")
        self.assertEqual((destination / "Test Album" / "01-song.mp3").stat().st_mtime_ns, song.stat().st_mtime_ns)
        self.assertTrue((destination / ".native-music.json").exists())
        self.assertEqual(song.read_bytes(), b"audio")
        self.assertEqual(self.downloads.run_once(scan_callback=lambda: True)["scan_pending"], [])
        self.assertEqual(self.downloads.run_once()["imported"], [])

    def test_traversal_and_symlink_are_rejected(self):
        self.downloads.submit(MAGNET)
        self._add_complete()
        self.qb.file_rows[HASH] = [{"name": "../escape.mp3", "size": 1, "progress": 1, "priority": 1}]
        result = self.downloads.run_once()
        self.assertEqual(result["errors"][0]["code"], "unsafe_source_path")

        link = self.download / "link"
        link.symlink_to(self.root)
        self.qb.file_rows[HASH] = [{"name": "link/song.mp3", "size": 1, "progress": 1, "priority": 1}]
        (self.root / "song.mp3").write_bytes(b"x")
        result = self.downloads.run_once()
        self.assertEqual(result["errors"][0]["code"], "unsafe_source_path")

    def test_user_collision_is_preserved_and_scan_retries(self):
        self.downloads.submit(MAGNET)
        self._add_complete()
        album = self.download / "Test Album"
        album.mkdir()
        (album / "01.mp3").write_bytes(b"x")
        self.qb.file_rows[HASH] = [{"name": "Test Album/01.mp3", "size": 1, "progress": 1, "priority": 1}]
        destination = self.library / "磁力下载" / ("Test Album-" + HASH[:12])
        destination.parent.mkdir()
        user_file = destination / "user.txt"
        destination.mkdir()
        user_file.write_text("keep")
        self.downloads.run_once()
        result = self.downloads.run_once()
        self.assertEqual(result["errors"][0]["code"], "library_collision")
        self.assertEqual(user_file.read_text(), "keep")

    def test_restart_after_atomic_stage_rename_recovers_own_manifest(self):
        self.downloads.submit(MAGNET)
        self._add_complete()
        base = self.downloads._import_base()
        stage = base / '.native-music-staging' / (".native-music-{}-restart".format(HASH[:12]))
        stage.mkdir(parents=True)
        (stage / "01.mp3").write_bytes(b"x")
        (stage / ".native-music.json").write_text(json.dumps({"version": 1, "hash": HASH,
                                                                 "files": [{"path": "01.mp3", "size": 1,
                                                                            "sha256": "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881"}]}))
        result = self.downloads.run_once(scan_callback=lambda: False)
        self.assertEqual(result["imported"], [HASH])
        destination = self.library / "磁力下载" / ("Test Album-" + HASH[:12])
        self.assertTrue((destination / ".native-music.json").exists())
        self.assertFalse(stage.exists())

    def test_recovery_does_not_publish_a_marker_without_its_audio(self):
        self.downloads.submit(MAGNET)
        self._add_complete()
        base = self.downloads._import_base()
        stage = base / '.native-music-staging' / ('.native-music-' + HASH[:12] + '-interrupted')
        stage.mkdir(parents=True)
        (stage / '.native-music.json').write_text(json.dumps({'version': 1, 'hash': HASH,
            'files': [{'path': 'missing.mp3', 'size': 5, 'sha256': 'a' * 64}]}))
        result = self.downloads.run_once()
        self.assertEqual(result['imported'], [])
        self.assertFalse((base / ('Test Album-' + HASH[:12])).exists())


if __name__ == "__main__":
    unittest.main()
