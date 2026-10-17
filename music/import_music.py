#!/usr/bin/env python3
"""One owner-user timer tick; never prints credentials or application tokens."""
import fcntl
import json
import os
from pathlib import Path
import sys

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))
sys.path.insert(0, str(APP_ROOT / 'music'))

from settings import get_settings, owner_is_valid
from torrent_import import MusicDownloads, MusicDownloadError
from music import native_session, _request


def main():
    settings = get_settings(APP_ROOT)
    if not owner_is_valid(settings, os.environ.get('USER', ''), os.getuid()):
        raise SystemExit('Native owner required')
    lock = os.open(APP_ROOT / 'private/music-import-run.lock', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return

        def scan():
            session = native_session(settings, {})
            if not session:
                return False
            connection, response = _request(settings, {}, 'POST', '/api/v1/scan',
                                            body=b'{"reimport":false}',
                                            auth='Bearer ' + session['access_token'])
            try:
                response.read(1024 * 1024)
                return response.status in {200, 201, 202}
            finally:
                connection.close()

        result = MusicDownloads(settings, APP_ROOT).run_once(scan_callback=scan)
        # Detailed status stays in the owner's private ledger. A timer log
        # contains only counts and safe fixed error codes.
        print(json.dumps({'imported': len(result.get('imported', [])),
                          'waiting': len(result.get('waiting', [])),
                          'errors': [item.get('code') for item in result.get('errors', [])],
                          'scan_pending': len(result.get('scan_pending', []))}))
    except MusicDownloadError as error:
        print(json.dumps({'error': error.code}))
        raise SystemExit(1)
    finally:
        os.close(lock)


if __name__ == '__main__':
    main()
