#!/usr/bin/python3
"""Native plugin.cgi entry. Do not log or expose native headers or secrets."""
import json
import base64
import http.client
import mimetypes
import os
from pathlib import Path
import sys
from urllib.parse import quote, unquote, urlsplit, urlencode

ROOT = Path(__file__).resolve().parent
APP_ROOT = ROOT.parent
sys.path.insert(0, str(APP_ROOT))
from settings import get_settings, owner_is_valid
SETTINGS = get_settings(APP_ROOT)
uri = os.environ.get('FILE_URI', '')
path = urlsplit(uri).path.lstrip('/')
app_override = os.environ.get('NATIVE_APP_ROUTE', '')
plugin_override = os.environ.get('NATIVE_PLUGIN_ID', '')
if app_override in ['pt', 'public', 'pansou']:
    path = app_override + '/' + (path or 'index.html')

def respond(status, content_type, body):
    if isinstance(body, str):
        body = body.encode('utf-8')
    sys.stdout.buffer.write(('Status: ' + status + '\r\nContent-Type: ' + content_type +
                             '\r\nCache-Control: no-store\r\nContent-Length: ' +
                             str(len(body)) + '\r\n\r\n').encode() + body)

def remembered_session(route):
    """Native owner gate stays mandatory. Only an app SID/JWT leaves the NAS."""
    import fcntl
    private = APP_ROOT / 'private'
    private.mkdir(mode=0o700, exist_ok=True)
    saved = private / 'native-login.json'
    method = os.environ.get('REQUEST_METHOD', 'GET')
    if method not in ['GET', 'POST', 'DELETE']:
        respond('405 Method Not Allowed', 'application/json', '{}')
        return
    length = int(os.environ.get('CONTENT_LENGTH') or '0')
    if length > 4096:
        respond('413 Payload Too Large', 'application/json', '{}')
        return
    lock = os.open(private / 'native-login.lock', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = json.loads(saved.read_text()) if saved.exists() else {}
        if method == 'DELETE':
            data.pop(route, None)
            payload = None
        else:
            payload = json.loads(sys.stdin.buffer.read(length)) if method == 'POST' else data.get(route)
            if not payload:
                respond('401 Unauthorized', 'application/json', '{"error":"Automatic login is disabled"}')
                return
            credentials = {key: payload[key] for key in ['username', 'password']}
            if not all(isinstance(v, str) and 0 < len(v) < 512 for v in credentials.values()):
                raise ValueError()
            port = SETTINGS['ports'][route]
            base = 'http://' + SETTINGS['nas_host'] + ':' + str(port)
            conn = http.client.HTTPConnection(SETTINGS['nas_host'], port, timeout=15)
            try:
                headers = {'Origin': base, 'Referer': base + '/'}
                if route == 'pansou':
                    headers['Content-Type'] = 'application/json'
                    conn.request('POST', '/api/auth/login', json.dumps(credentials).encode(), headers)
                else:
                    headers['Content-Type'] = 'application/x-www-form-urlencoded'
                    conn.request('POST', '/api/v2/auth/login', urlencode(credentials), headers)
                response = conn.getresponse()
                raw = response.read()
                if response.status not in [200, 204]:
                    raise ValueError()
                if route == 'pansou':
                    result = json.loads(raw)
                    token = result.get('token') or result.get('data', {}).get('token')
                    answer = {'token': token, 'username': credentials['username']}
                else:
                    cookie_name = 'QBT_SID_' + str(port)
                    sid = next((v.split(';', 1)[0].split('=', 1)[1] for k, v in response.getheaders()
                                if k.lower() == 'set-cookie' and v.startswith(cookie_name + '=')), '')
                    answer = {'session': sid}
                if not all(answer.values()):
                    raise ValueError()
            finally:
                conn.close()
            if method == 'POST':
                if payload.get('remember') is True:
                    data[route] = credentials
                else:
                    data.pop(route, None)
        if method in ['POST', 'DELETE']:
            temp = saved.with_suffix('.tmp')
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as f:
                json.dump(data, f)
            os.replace(temp, saved)
        respond('200 OK', 'application/json', json.dumps(answer if payload else {'remembered': False}))
    except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
        respond('401 Unauthorized', 'application/json', '{"error":"Application login was not confirmed"}')
    finally:
        os.close(lock)

def native_download():
    """Owner-only explicit magnet submission to a fixed existing downloader."""
    from urllib.parse import parse_qs
    import time
    method = os.environ.get('REQUEST_METHOD', 'GET')
    def fail(code, message):
        respond(code, 'application/json', json.dumps({'error': message}, ensure_ascii=False))
    if method not in ['GET', 'POST']:
        fail('405 Method Not Allowed', '不支持此操作')
        return
    saved = APP_ROOT / 'private/native-login.json'
    try:
        credentials = json.loads(saved.read_text())
        length = int(os.environ.get('CONTENT_LENGTH') or '0')
        if length > 32768:
            fail('413 Payload Too Large', '磁力链接过长')
            return
        data = json.loads(sys.stdin.buffer.read(length)) if method == 'POST' else {'downloader': parse_qs(urlsplit(uri).query or os.environ.get('QUERY_STRING', '')).get('downloader', ['public'])[0]}
        app = data.get('downloader', 'public')
        if app not in ['public', 'pt']:
            fail('400 Bad Request', '请选择有效的下载器')
            return
        cred = credentials.get(app)
        if not cred:
            fail('401 Unauthorized', '请先在此下载器登录并开启记住登录')
            return
        port = SETTINGS['ports'][app]
        conn = http.client.HTTPConnection(SETTINGS['nas_host'], port, timeout=20)
        headers = {'Origin': 'http://' + SETTINGS['nas_host'] + ':' + str(port),
                   'Referer': 'http://' + SETTINGS['nas_host'] + ':' + str(port) + '/',
                   'Content-Type': 'application/x-www-form-urlencoded'}
        conn.request('POST', '/api/v2/auth/login', urlencode({k: cred[k] for k in ['username', 'password']}), headers)
        login = conn.getresponse();login.read()
        cookie_name = 'QBT_SID_' + str(port)
        sid = next((v.split(';', 1)[0] for k, v in login.getheaders()
                    if k.lower() == 'set-cookie' and v.startswith(cookie_name + '=')), '')
        if not sid:
            conn.close();fail('401 Unauthorized', '下载器登录未通过，请重新登录下载器')
            return
        headers['Cookie'] = sid
        def qb(path, body=None):
            conn.request('POST' if body is not None else 'GET', '/api/v2/' + path,
                         urlencode(body) if body is not None else None, headers)
            r = conn.getresponse();raw = r.read()
            if r.status not in [200, 204]: raise ValueError('下载器请求失败')
            try: return json.loads(raw)
            except ValueError: return raw.decode()
        prefs = qb('app/preferences')
        if method == 'GET':
            respond('200 OK', 'application/json', json.dumps({'downloaders': [
                {'id': 'public', 'name': '公共下载器（代理）'},
                {'id': 'pt', 'name': 'PT 下载器（直连，仅私有资源）'}], 'default': 'public', 'save_path': prefs['save_path']}, ensure_ascii=False))
            conn.close();return
        magnet = data.get('magnet', '')
        if not isinstance(magnet, str) or not magnet.startswith('magnet:?') or '\n' in magnet or '\r' in magnet:
            raise ValueError('仅支持磁力链接')
        xts = parse_qs(urlsplit(magnet).query).get('xt', [])
        hashes = []
        import re
        for xt in xts:
            if xt.lower().startswith('urn:btih:'):
                h = xt[9:]
                if re.fullmatch('[A-Fa-f0-9]{40}', h): hashes.append(h.lower())
                elif re.fullmatch('[A-Za-z2-7]{32}', h): hashes.append(base64.b32decode(h.upper()).hex())
            elif re.fullmatch('urn:btmh:1220[A-Fa-f0-9]{64}', xt): hashes.append(xt[13:].lower())
        if not hashes: raise ValueError('磁力链接缺少有效的种子信息')
        rows = qb('torrents/info')
        existing = next((t for t in rows if any(h in [t.get('hash'), t.get('infohash_v1'), t.get('infohash_v2')] for h in hashes)), None)
        if existing:
            respond('200 OK', 'application/json', json.dumps({'status': 'exists', 'name': existing['name'], 'downloader': app, 'save_path': existing['save_path']}, ensure_ascii=False))
            conn.close();return
        state = qb('sync/maindata').get('server_state', {})
        remaining = sum(max(0, t.get('amount_left', 0)) for t in rows)
        if state.get('free_space_on_disk', 0) - remaining < 10 * 1024**3:
            conn.close();fail('409 Conflict', '现有下载完成后不足 10 GiB 空间，暂不新增')
            return
        if prefs.get('up_limit') != 1048576 or (app == 'pt' and prefs.get('proxy_type') != 'None'):
            conn.close();fail('409 Conflict', '下载器限速或 PT 直连设置已变化，请先核对')
            return
        result = qb('torrents/add', {'urls': magnet, 'savepath': prefs['save_path'], 'stopped': 'false'})
        accepted = (isinstance(result, dict) and result.get('failure_count', 0) == 0 and result.get('success_count', 0) + result.get('pending_count', 0) > 0) or (isinstance(result, str) and result.strip() in ['', 'Ok.'])
        if not accepted: raise ValueError('下载器未接受此链接')
        found = None
        for attempt in range(5):
            items = qb('torrents/info')
            found = next((t for t in items if any(h in [t.get('hash'), t.get('infohash_v1'), t.get('infohash_v2')] for h in hashes)), None)
            if found: break
            time.sleep(.3)
        respond('200 OK', 'application/json', json.dumps({'status': 'added' if found else 'accepted',
                'downloader': app, 'name': found['name'] if found else '', 'save_path': prefs['save_path']}, ensure_ascii=False))
        conn.close()
    except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException) as e:
        # Fixed human-facing errors; never expose credentials or HTTP headers.
        safe = str(e) if isinstance(e, ValueError) and str(e) in ['仅支持磁力链接', '磁力链接缺少有效的种子信息', '下载器未接受此链接', '下载器请求失败'] else '下载器暂时不可用，请稍后重试'
        fail('400 Bad Request', safe)

# plugin.cgi and the vendor fcgi gate validate the native login before exec.
# Keep an additional fixed owner check; no aliases bypass the gate.
if not owner_is_valid(SETTINGS, os.environ.get('USER', ''), os.getuid()):
    respond('403 Forbidden', 'text/plain', 'Native owner login required')
elif path == 'pansou/native/download':
    native_download()
elif path in ['pt/native/session', 'public/native/session', 'pansou/native/session']:
    remembered_session(path.split('/')[0])
elif app_override in ['pt', 'public', 'pansou'] and path == app_override + '/index.html' and \
        'embedded=1' not in os.environ.get('QUERY_STRING', ''):
    html = (ROOT / 'moviepilot-frame.html').read_text()
    html = html.replace('moviepilot/index.html#/', 'vendor-ui/index.html' if app_override == 'pansou' else 'index.html?embedded=1')
    html = html.replace('MoviePilot 资源管理', '下载与资源管理')
    respond('200 OK', 'text/html; charset=utf-8', html)
elif path in ['icon.png', 'pt/icon.png', 'public/icon.png', 'pansou/icon.png']:
    plugin = plugin_override if plugin_override in ['qbpt', 'qbpublic', 'pansou'] else 'mediastack'
    icon = APP_ROOT / 'assets' / (plugin + '.png')
    respond('200 OK', 'image/png', icon.read_bytes())
elif path == 'status.json':
    respond('200 OK', 'application/json', json.dumps({'native_user_ok': True,
            'cgi_owner_uid_ok': True, 'application_routes': 'enabled'}))
elif path in ['pt/index.html', 'public/index.html']:
    respond('200 OK', 'text/html; charset=utf-8', (ROOT / 'qb-native.html').read_bytes())
elif path == 'pansou/index.html':
    respond('200 OK', 'text/html; charset=utf-8', (ROOT / 'pansou.html').read_bytes())
elif path in ['bridge-auth.js', 'moviepilot/bridge-auth.js']:
    respond('200 OK', 'application/javascript', (ROOT / 'bridge-auth.js').read_bytes())
elif path == 'native-bridge.js' or path in ['pt/native-bridge.js', 'public/native-bridge.js', 'pansou/native-bridge.js']:
    respond('200 OK', 'application/javascript', (ROOT / 'native-bridge.js').read_bytes())
elif path in ['pt/qb-native.js', 'public/qb-native.js', 'pt/qb-native.css', 'public/qb-native.css']:
    asset = path.rsplit('/', 1)[1]
    respond('200 OK', 'application/javascript' if asset.endswith('.js') else 'text/css', (ROOT / asset).read_bytes())
elif path in ['pt/qb-ui-bridge.js', 'public/qb-ui-bridge.js']:
    respond('200 OK', 'application/javascript', (ROOT / 'qb-ui-bridge.js').read_bytes())
elif path in ['pansou/pansou-native.js', 'pansou/pansou-native.css']:
    asset = path.rsplit('/', 1)[1]
    respond('200 OK', 'application/javascript' if asset.endswith('.js') else 'text/css', (ROOT / asset).read_bytes())
elif path == 'pansou/pansou-ui-bridge.js':
    respond('200 OK', 'application/javascript', (ROOT / 'pansou-ui-bridge.js').read_bytes())
elif path.startswith(('pt/vuetorrent/', 'public/vuetorrent/')) and '/api/v2/' not in path:
    relative = unquote(path.split('/vuetorrent/', 1)[1])
    target_file = (ROOT / 'vuetorrent' / relative).resolve()
    if not target_file.is_relative_to((ROOT / 'vuetorrent').resolve()) or not target_file.is_file():
        respond('404 Not Found', 'text/plain', 'Unknown UI asset')
    else:
        respond('200 OK', mimetypes.guess_type(str(target_file))[0] or 'application/octet-stream', target_file.read_bytes())
elif path in ['', 'index.html']:
    respond('200 OK', 'text/html; charset=utf-8', (ROOT / 'moviepilot-frame.html').read_bytes())
else:
    routes = {'moviepilot': (SETTINGS['ports']['moviepilot'], 'MoviePilot'),
              'pt': (SETTINGS['ports']['pt'], 'QBT_SID_' + str(SETTINGS['ports']['pt'])),
              'public': (SETTINGS['ports']['public'], 'QBT_SID_' + str(SETTINGS['ports']['public'])),
              'pansou': (SETTINGS['ports']['pansou'], '')}
    route, _, rest = path.partition('/')
    if route in ['pt', 'public'] and rest.startswith('vuetorrent/api/v2/'):
        rest = rest.removeprefix('vuetorrent/')
    if route == 'pansou' and rest.startswith('vendor-ui/'):
        rest = rest.removeprefix('vendor-ui/')
    # Xiaomi's micro-app sandbox resolves relative API calls against the
    # plugin root even when index.html lives one level deeper.
    if path.startswith('api/v1/'):
        route, rest = 'moviepilot', path
    if route not in routes or '..' in unquote(path).split('/') or '\\' in path:
        respond('404 Not Found', 'text/plain', 'Unknown application route')
    else:
        port, cookie_name = routes[route]
        query = urlsplit(uri).query or os.environ.get('QUERY_STRING', '')
        target = '/' + rest + ('?' + query if query else '')
        # Vendor CGI may decode a UTF-8 route before passing FILE_URI.
        # Preserve existing percent escapes while encoding non-ASCII characters.
        target = quote(target, safe="/%?=&:+,;@!$'()*-._~")
        method = os.environ.get('REQUEST_METHOD', 'GET')
        length = int(os.environ.get('CONTENT_LENGTH') or '0')
        if method not in ['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'] or length > 8388608:
            respond('413 Payload Too Large', 'text/plain', 'Unsupported request')
            sys.exit()
        headers = {'Host': SETTINGS['nas_host'] + ':' + str(port),
                   'Origin': 'http://' + SETTINGS['nas_host'] + ':' + str(port),
                   'Referer': 'http://' + SETTINGS['nas_host'] + ':' + str(port) + '/',
                   'Accept-Encoding': 'identity'}
        for env, header in [('CONTENT_TYPE', 'Content-Type'), ('HTTP_ACCEPT', 'Accept')]:
            if os.environ.get(env):
                headers[header] = os.environ[env]
        # Never send native login tokens to an application. Forward only the
        # application's known cookie name or an explicit application JWT header.
        cookies = [x.strip() for x in os.environ.get('HTTP_COOKIE', '').split(';')
                   if cookie_name and x.strip().startswith(cookie_name + '=')]
        if cookies:
            headers['Cookie'] = '; '.join(cookies)
        # Native desktop file:// shells cannot rely on third-party cookies.
        # The app's existing qB session is kept only in this page's memory.
        session = os.environ.get('HTTP_X_APPLICATION_SESSION', '')
        if route in ['pt', 'public'] and session:
            import re
            if re.fullmatch(r'[A-Za-z0-9_+/=-]{20,128}', session):
                headers['Cookie'] = cookie_name + '=' + session
        app_auth = os.environ.get('HTTP_X_APPLICATION_AUTHORIZATION', '')
        if app_auth.startswith('Bearer '):
            headers['Authorization'] = app_auth
        else:
            auth = os.environ.get('HTTP_AUTHORIZATION', '')
            try:
                token = auth.removeprefix('Bearer ').split('.')[1]
                claims = json.loads(base64.urlsafe_b64decode(token + '=' * (-len(token) % 4)))
                if auth.startswith('Bearer ') and claims.get('purpose') == 'authentication':
                    headers['Authorization'] = auth
            except (IndexError, ValueError, TypeError):
                pass
        try:
            # Existing ports bind only the NAS LAN address, not loopback.
            # Keep this fixed same-NAS destination; never accept a target URL.
            conn = http.client.HTTPConnection(SETTINGS['nas_host'], port, timeout=60)
            conn.request(method, target, body=sys.stdin.buffer.read(length) if length else None,
                         headers=headers)
            upstream = conn.getresponse()
            body = upstream.read()
            content_type = upstream.getheader('Content-Type', '')
            if route in ['pt', 'public'] and rest == 'api/v2/auth/login':
                sid = ''
                for key, value in upstream.getheaders():
                    if key.lower() == 'set-cookie' and value.startswith(cookie_name + '='):
                        sid = value.split(';', 1)[0].split('=', 1)[1]
                respond('200 OK' if sid else '401 Unauthorized', 'application/json',
                        json.dumps({'session': sid} if sid else {'error': 'Login failed'}))
                conn.close()
                sys.exit()
            if route == 'pansou' and 'text/html' in content_type and rest in ['', 'index.html']:
                import re
                html = body.decode('utf-8')
                html = html.replace('</head>', '<link rel="stylesheet" href="../pansou-native.css"><script defer src="../pansou-native.js"></script></head>')
                html = re.sub(r'(href|src)="/(assets/[^"]+|favicon.ico)"', r'\1="./\2"', html)
                # Bootstrap native auto-login before the official frontend checks auth.
                html = re.sub(r'<script type="module"[^>]*src="([^"]+)"[^>]*></script>',
                    lambda m: '<script src="../pansou-ui-bridge.js"></script><script type="module">'
                    'await window.nativePanReady; import(' + json.dumps(m.group(1)) + ');</script>', html)
                body = html.encode('utf-8')
            if route == 'moviepilot' and 'text/html' in content_type and rest in ['', 'index.html']:
                body = body.replace(b'<head>', b'<head><script src="./bridge-auth.js"></script>', 1)
            prefix = f"/plugin/{SETTINGS['plugin_namespace']}/mediastack/{route}/"
            cookie_prefix = f"/plugin/{SETTINGS['plugin_namespace']}/mediastack/" if route == 'moviepilot' else prefix
            if plugin_override in ['qbpt', 'qbpublic', 'pansou']:
                prefix = f"/plugin/{SETTINGS['plugin_namespace']}/{plugin_override}/"
                cookie_prefix = prefix
            lines = ['Status: ' + str(upstream.status) + ' ' + upstream.reason]
            for k, v in upstream.getheaders():
                if k.lower() in ['connection', 'transfer-encoding', 'content-length', 'content-encoding']:
                    continue
                if k.lower() == 'set-cookie':
                    if not v.startswith(cookie_name + '='):
                        continue
                    v = v.replace('Path=/', 'Path=' + cookie_prefix)
                elif k.lower() == 'location':
                    if v.startswith('/'):
                        v = prefix + v.lstrip('/')
                    elif v.startswith('http'):
                        # Reject external redirects rather than follow them.
                        continue
                lines.append(k + ': ' + v)
            lines += ['Content-Length: ' + str(len(body)), 'Cache-Control: no-store']
            sys.stdout.buffer.write(('\r\n'.join(lines) + '\r\n\r\n').encode() + body)
            conn.close()
        except (OSError, http.client.HTTPException):
            respond('502 Bad Gateway', 'text/plain', 'Application temporarily unavailable')
