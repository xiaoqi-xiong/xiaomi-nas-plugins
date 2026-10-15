// Native integration for the deployed PanSou frontend: fixed API routes and
// owner-gated remembered login. Tokens stay in this iframe's memory.
(() => {
  const base = new URL('.', location.href);
  const endpoint = new URL('../native/session', base);
  const originalFetch = window.fetch.bind(window);
  const memory = new Map();
  const storage = window.localStorage;
  const originalGet = Storage.prototype.getItem;
  const originalSet = Storage.prototype.setItem;
  const originalRemove = Storage.prototype.removeItem;
  const authKey = key => key === 'auth_token' || key === 'auth_username';
  for (const key of ['auth_token', 'auth_username']) originalRemove.call(storage, key);
  Storage.prototype.getItem = function(key) {
    return this === storage && authKey(key) ? memory.get(key) || null : originalGet.call(this, key);
  };
  Storage.prototype.setItem = function(key, value) {
    if (this === storage && authKey(key)) memory.set(key, String(value));
    else originalSet.call(this, key, value);
  };
  Storage.prototype.removeItem = function(key) {
    if (this === storage && authKey(key)) memory.delete(key);
    else originalRemove.call(this, key);
  };
  document.addEventListener('click', e => {
    const a=e.target.closest('a[href="/report.html"]');
    if(a) a.href=new URL('../report.html',base).href;
  },true);
  const ready = window.nativePanReady = originalFetch(endpoint, {cache: 'no-store'})
    .then(r => r.ok ? r.json() : null)
    .then(j => {
      if (j?.token) {
        memory.set('auth_token', j.token);
        memory.set('auth_username', j.username || 'admin');
      }
    }).catch(() => {});
  function mapped(input) {
    const u = new URL(input, base);
    if (u.origin !== base.origin) return null;
    const match = u.pathname.match(/\/(api|qqpd|gying|panlian|weibo|woniu)(\/.*|$)/);
    if (!match) return null;
    const path = match[1] + match[2];
    return {path, url: new URL('../' + path + u.search, base).href};
  }
  async function remember(body) {
    let data;
    try {data = typeof body === 'string' ? JSON.parse(body) : null;} catch {return;}
    if (!data?.username || !data?.password) return;
    await originalFetch(endpoint, {method: 'POST', cache: 'no-store',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({...data, remember: true})});
  }
  async function forget() {
    const r = await originalFetch(endpoint, {method: 'DELETE', cache: 'no-store'});
    if (!r.ok) throw Error('自动登录未能关闭，请重试');
    memory.clear();
  }
  const OriginalXHR = window.XMLHttpRequest;
  window.XMLHttpRequest = class extends OriginalXHR {
    open(method, url, ...args) {
      this.nativeRoute = mapped(url);
      return super.open(method, this.nativeRoute?.url || url, ...args);
    }
    setRequestHeader(key, value) {
      if (this.nativeRoute && key.toLowerCase() === 'authorization') {
        this.nativeAuth = value;
        return;
      }
      return super.setRequestHeader(key, value);
    }
    send(body) {
      if (!this.nativeRoute) return super.send(body);
      const route = this.nativeRoute.path;
      if (route === 'api/auth/login') this.addEventListener('load', () => {
        if (this.status >= 200 && this.status < 300) remember(body).catch(() => {});
      }, {once: true});
      ready.then(async () => {
        if (route === 'api/auth/logout') await forget();
        const auth = this.nativeAuth || (memory.get('auth_token') ? 'Bearer ' + memory.get('auth_token') : '');
        if (auth) super.setRequestHeader('X-Application-Authorization', auth);
        super.send(body);
      }).catch(() => this.dispatchEvent(new Event('error')));
    }
  };
  window.fetch = async (input, init = {}) => {
    const route = mapped(input instanceof Request ? input.url : input);
    if (!route) return originalFetch(input, init);
    await ready;
    if (route.path === 'api/auth/logout') await forget();
    const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
    const auth = headers.get('Authorization') || (memory.get('auth_token') ? 'Bearer ' + memory.get('auth_token') : '');
    headers.delete('Authorization');
    if (auth) headers.set('X-Application-Authorization', auth);
    const r = await originalFetch(route.url, {...init, headers, cache: 'no-store'});
    if (route.path === 'api/auth/login' && r.ok) await remember(init.body);
    return r;
  };
})();
