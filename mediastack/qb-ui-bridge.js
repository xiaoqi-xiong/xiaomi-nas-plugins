// Keep the native owner gate and per-qB session separate from Xiaomi login.
// VueTorrent remains the upstream release; this adapter only fixes native paths.
(() => {
  const base = new URL('.', location.href);
  // Upstream persistence hydrates a plain JSON partial state. Seed only new clients.
  if (!localStorage.getItem('webuiSettings')) {
    localStorage.setItem('webuiSettings', JSON.stringify({language: 'zh-Hans', useBinarySize: true}));
  }
  const nativeEndpoint = new URL('../native/session', base);
  const originalFetch = window.fetch.bind(window);
  let session = '';
  const ready = originalFetch(nativeEndpoint, {cache: 'no-store'})
    .then(r => r.ok ? r.json() : null)
    .then(j => { session = j?.session || ''; })
    .catch(() => {});
  function mapped(input) {
    const u = new URL(input, base);
    const match = u.pathname.match(/\/api\/v2\/(.*)$/);
    if (!match || u.origin !== base.origin) return null;
    return {url: new URL('../api/v2/' + match[1] + u.search, base).href, path: match[1]};
  }
  async function remember(body) {
    const form = new URLSearchParams(body);
    const r = await originalFetch(nativeEndpoint, {method: 'POST', cache: 'no-store',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username: form.get('username'), password: form.get('password'), remember: true})});
    if (r.ok) session = (await r.json()).session || session;
  }
  async function forget() {
    const r = await originalFetch(nativeEndpoint, {method: 'DELETE', cache: 'no-store'});
    if (!r.ok) throw Error('自动登录未能关闭，请重试');
  }
  const OriginalXHR = window.XMLHttpRequest;
  window.XMLHttpRequest = class extends OriginalXHR {
    open(method, url, ...args) {
      this.nativeRoute = mapped(url);
      return super.open(method, this.nativeRoute?.url || url, ...args);
    }
    send(body) {
      if (!this.nativeRoute) return super.send(body);
      const route = this.nativeRoute.path;
      if (route === 'auth/login') this.addEventListener('load', () => {
        if (this.status >= 200 && this.status < 300) {
          try { session = JSON.parse(this.responseText).session || ''; } catch {}
          remember(body).catch(() => {});
        }
      }, {once: true});
      if (route === 'auth/logout') this.addEventListener('load', () => {session = '';}, {once: true});
      ready.then(async () => {
        if (route === 'auth/logout') await forget();
        if (session && route !== 'auth/login') super.setRequestHeader('X-Application-Session', session);
        super.send(body);
      }).catch(() => this.dispatchEvent(new Event('error')));
    }
  };
  window.fetch = async (input, init = {}) => {
    const route = mapped(input instanceof Request ? input.url : input);
    if (!route) return originalFetch(input, init);
    await ready;
    if (route.path === 'auth/logout') await forget();
    const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
    if (session) headers.set('X-Application-Session', session);
    const r = await originalFetch(route.url, {...init, headers, cache: 'no-store'});
    if (route.path === 'auth/logout') session = '';
    return r;
  };
})();
