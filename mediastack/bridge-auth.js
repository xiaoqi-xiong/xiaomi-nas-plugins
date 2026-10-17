// Scope this adapter to the MoviePilot micro-app window. Xiaomi reserves the
// Authorization header for native login; carry the app's separate Bearer token
// in a separate header. The owner-checked CGI restores it only for MoviePilot.
(() => {
  const OriginalXHR = window.XMLHttpRequest;
  const originalFetch = window.fetch.bind(window);
  const storage = window.localStorage;
  const originalGetItem = Storage.prototype.getItem;
  const originalSetItem = Storage.prototype.setItem;
  const originalRemoveItem = Storage.prototype.removeItem;
  const appRoot = new URL('./', location.href);
  const sessionEndpoint = new URL('native/session', appRoot);
  const volatile = {auth: undefined, user: undefined};
  let forgetting = null;

  function appUrl(input) {
    return new URL(input instanceof Request ? input.url : input, location.href);
  }

  function isAppRequest(input) {
    try {
      const url = appUrl(input);
      return url.origin === appRoot.origin && /\/api\//.test(url.pathname);
    } catch (_) { return false; }
  }

  function clearPersistedState() {
    originalRemoveItem.call(storage, 'auth');
    originalRemoveItem.call(storage, 'user');
  }

  function hydrate(value) {
    clearPersistedState();
    volatile.auth = value && value.auth ? JSON.stringify(value.auth) : null;
    volatile.user = value && value.user ? JSON.stringify(value.user) : null;
    return value;
  }

  function forget() {
    if (forgetting) return forgetting;
    forgetting = originalFetch(sessionEndpoint, {
      method: 'DELETE', cache: 'no-store', keepalive: true,
    }).finally(() => { forgetting = null; });
    return forgetting;
  }

  Storage.prototype.getItem = function(key) {
    if (this === storage && (key === 'auth' || key === 'user') && volatile[key] !== undefined)
      return volatile[key];
    return originalGetItem.call(this, key);
  };
  Storage.prototype.setItem = function(key, value) {
    if (this === storage && (key === 'auth' || key === 'user')) {
      volatile[key] = String(value);
      if (key === 'auth') {
        try {
          const auth = JSON.parse(volatile.auth);
          if (!auth?.token) forget().catch(() => {});
        } catch (_) { /* Keep malformed app state in memory only. */ }
      }
      return;
    }
    return originalSetItem.call(this, key, value);
  };
  Storage.prototype.removeItem = function(key) {
    if (this === storage && (key === 'auth' || key === 'user')) {
      volatile[key] = null;
      if (key === 'auth') forget().catch(() => {});
      return;
    }
    return originalRemoveItem.call(this, key);
  };

  async function nativeResponse(response) {
    if (!response.ok) {
      hydrate(null);
      return response;
    }
    const value = await response.json();
    hydrate(value);
    // The test/native shims and the browser both only need the JSON body
    // once; retain a compatible response object after consuming it.
    response.json = async () => value;
    return response;
  }

  window.nativeMoviePilotReady = originalFetch(sessionEndpoint, {cache: 'no-store'})
    .then(nativeResponse)
    .catch(() => { hydrate(null); return null; });

  window.XMLHttpRequest = class extends OriginalXHR {
    open(method, input, ...rest) {
      this.nativeMoviePilotAPI = isAppRequest(input);
      return super.open(method, input, ...rest);
    }
    setRequestHeader(name, value) {
      if (this.nativeMoviePilotAPI && name.toLowerCase() === 'authorization' && String(value).startsWith('Bearer '))
        name = 'X-Application-Authorization';
      return super.setRequestHeader(name, value);
    }
  };

  window.fetch = async (input, init = {}) => {
    let url;
    try { url = appUrl(input); } catch (_) { return originalFetch(input, init); }
    if (!isAppRequest(input)) return originalFetch(input, init);
    const method = String(init.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    if (method === 'POST' && /\/api\/v1\/login\/access-token$/.test(url.pathname)) {
      const body = init.body || (input instanceof Request ? input.body : null);
      const form = body && typeof body.get === 'function' ? body : null;
      const remember = !!document.querySelector('input[type="checkbox"]')?.checked;
      const payload = {
        username: String(form?.get('username') || ''),
        password: String(form?.get('password') || ''),
        remember,
      };
      const response = await originalFetch(sessionEndpoint, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload), cache: 'no-store',
      });
      return nativeResponse(response);
    }
    const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
    const auth = headers.get('Authorization');
    if (auth?.startsWith('Bearer ')) {
      headers.set('X-Application-Authorization', auth);
      headers.delete('Authorization');
    }
    return originalFetch(input, {...init, headers});
  };
})();
