const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const code = fs.readFileSync(require('node:path').join(__dirname, '../mediastack/bridge-auth.js'), 'utf8');

function page({enabled = true, remember = true} = {}) {
  class Storage {
    constructor() { this.values = new Map(); }
    getItem(key) { return this.values.get(key) ?? null; }
    setItem(key, value) { this.values.set(key, String(value)); }
    removeItem(key) { this.values.delete(key); }
  }
  const storage = new Storage();
  storage.setItem('auth', '{"token":"stale-token"}');
  storage.setItem('theme', 'dark');
  const calls = [];
  class XHR {
    constructor() { this.headers = {}; this.listeners = {}; }
    open(method, url) { this.url = url; }
    setRequestHeader(name, value) { this.headers[name] = value; }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    send(body) { this.body = body; }
  }
  const context = vm.createContext({URL, Headers, Request, FormData, URLSearchParams, Storage,
    location: {href: 'https://nas/plugin/example-owner/mediastack/moviepilot/index.html'},
    document: {currentScript: null, querySelector: () => ({checked: remember})},
    window: {localStorage: storage, XMLHttpRequest: XHR, alert: assert.fail,
      fetch: async (input, init = {}) => {
        calls.push({url: String(input), init});
        return {ok: enabled || init.method === 'DELETE', json: async () => ({
          auth: {token: 'fresh-token', remember: true, originalPath: null},
          user: {superUser: true, userID: 1, userName: 'admin', permissions: {}},
        })};
      }},
  });
  vm.runInContext(code, context);
  return {window: context.window, storage, calls};
}

test('empty or stale browser state hydrates fresh app auth without persisting tokens', async () => {
  const {window, storage, calls} = page();
  await window.nativeMoviePilotReady;
  assert.equal(JSON.parse(storage.getItem('auth')).token, 'fresh-token');
  assert.equal(JSON.parse(storage.getItem('user')).userID, 1);
  assert.equal(storage.values.has('auth'), false);
  assert.equal(storage.values.has('user'), false);
  assert.equal(storage.getItem('theme'), 'dark');
  assert.equal(calls.length, 1);
  assert.ok(calls[0].url.endsWith('/moviepilot/native/session'));
});

test('disabled auto-login leaves the official login page without a stale session', async () => {
  const {window, storage} = page({enabled: false});
  await window.nativeMoviePilotReady;
  assert.equal(storage.getItem('auth'), null);
  assert.equal(storage.getItem('user'), null);
});

test('official logout forgets the owner credential and reopening cannot silently restore it', async () => {
  const {window, storage, calls} = page();
  await window.nativeMoviePilotReady;
  storage.setItem('auth', JSON.stringify({token: null, remember: true}));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls.filter(c => c.init.method === 'DELETE').length, 1);
  assert.equal(JSON.parse(storage.getItem('auth')).token, null);
});

test('only same-origin app API Bearer headers are separated from native authentication', async () => {
  const {window, calls} = page();
  await window.nativeMoviePilotReady;
  await window.fetch('./api/v1/user/', {headers: {Authorization: 'Bearer app-token'}});
  assert.equal(calls.at(-1).init.headers.get('Authorization'), null);
  assert.equal(calls.at(-1).init.headers.get('X-Application-Authorization'), 'Bearer app-token');
  await window.fetch('https://third-party.invalid/api/v1/example', {headers: {Authorization: 'Bearer external'}});
  assert.equal(calls.at(-1).init.headers.Authorization, 'Bearer external');
  const xhr = new window.XMLHttpRequest();
  xhr.open('GET', './api/v1/user/');
  xhr.setRequestHeader('Authorization', 'Bearer app-token');
  assert.equal(xhr.headers['X-Application-Authorization'], 'Bearer app-token');
});

test('manual login follows the official remember-login checkbox', async () => {
  for (const remember of [false, true]) {
    const {window, calls} = page({remember});
    await window.nativeMoviePilotReady;
    const form = new FormData(); form.set('username', 'admin'); form.set('password', 'fake-password');
    await window.fetch('./api/v1/login/access-token', {method: 'POST', body: form});
    const saved = calls.find(c => c.url.endsWith('/native/session') && c.init.method === 'POST');
    assert.equal(JSON.parse(saved.init.body).remember, remember);
  }
});
