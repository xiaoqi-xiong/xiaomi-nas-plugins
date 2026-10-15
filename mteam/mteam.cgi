#!/usr/bin/python3
"""Owner-scoped static entry for the M-Team official-site wrapper."""
import mimetypes
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent
APP_ROOT = ROOT.parent
sys.path.insert(0, str(APP_ROOT))
from settings import get_settings, owner_is_valid
SETTINGS = get_settings(APP_ROOT)
raw_path = urlsplit(os.environ.get('FILE_URI', '')).path
path = unquote(raw_path).lstrip('/')
if '/mteam/' in path:
    path = path.split('/mteam/', 1)[1]

def respond(status, content_type, body):
    if isinstance(body, str):
        body = body.encode('utf-8')
    sys.stdout.buffer.write(
        ('Status: ' + status + '\r\nContent-Type: ' + content_type +
         '\r\nCache-Control: no-store\r\nContent-Length: ' +
         str(len(body)) + '\r\n\r\n').encode() + body)

if not owner_is_valid(SETTINGS, os.environ.get('USER', ''), os.getuid()):
    respond('403 Forbidden', 'text/plain; charset=utf-8', 'Native owner login required')
elif path in ['', 'index.html', 'native-bridge.js', 'icon.png']:
    asset = 'index.html' if path == '' else path
    target = ROOT / asset
    kind = mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
    if asset.endswith('.js'):
        kind = 'application/javascript'
    elif asset.endswith('.html'):
        kind = 'text/html; charset=utf-8'
    respond('200 OK', kind, target.read_bytes())
else:
    respond('404 Not Found', 'text/plain; charset=utf-8', 'Unknown M-Team asset')
