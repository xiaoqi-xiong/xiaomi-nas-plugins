// Adapter for the official Songloft Flutter Web client bundled with v2.13.3.
// App tokens are separate from native Xiaomi authorization. No password is
// injected into the frontend, and media URLs do not carry an application JWT.
(() => {
  const base = new URL('.', document.currentScript?.src || location.href);
  const endpoint = new URL('music.cgi?api=session', base);
  const originalFetch = window.fetch.bind(window);
  const storage = window.localStorage;
  const originalRemove = Storage.prototype.removeItem;
  let forgetting = null;
  const forget = () => forgetting || (forgetting = originalFetch(endpoint, {
    method: 'DELETE', cache: 'no-store', keepalive: true,
  }).then(r => {
    if (!r.ok) throw Error('自动登录未能关闭，请重试');
  }).finally(() => { forgetting = null; }));
  Storage.prototype.removeItem = function(key) {
    if (this === storage && key === 'flutter.access_token') forget().catch(e => window.alert(e.message));
    return originalRemove.call(this, key);
  };
  window.nativeSongloftReady = originalFetch(endpoint, {cache: 'no-store'})
    .then(r => r.ok ? r.json() : null)
    .then(j => {
      if (!j?.access_token) {
        for (const key of ['access_token', 'refresh_token', 'token_expires_at'])
          originalRemove.call(storage, 'flutter.' + key);
        return;
      }
      // SharedPreferences Web serializes its values as JSON under flutter.*.
      storage.setItem('flutter.access_token', JSON.stringify(j.access_token));
      storage.setItem('flutter.refresh_token', JSON.stringify(j.refresh_token));
      storage.setItem('flutter.token_expires_at', JSON.stringify(new Date(Date.now() + (j.expires_in || 3600) * 1000).toISOString()));
    }).catch(() => {});

  function mapped(input) {
    const url = new URL(input, location.href);
    if (url.origin !== base.origin) return null;
    const index = url.pathname.indexOf('/api/v1/');
    if (index < 0) return null;
    const apiPath = url.pathname.slice(index + 1);
    const target = new URL('vendor-ui/' + apiPath, base);
    url.searchParams.delete('access_token'); url.searchParams.delete('token');
    target.search = url.search;
    return target.href;
  }
  const OriginalXHR = window.XMLHttpRequest;
  window.XMLHttpRequest = class extends OriginalXHR {
    open(method, input, ...rest) {
      const target = mapped(input);
      this.nativeMusicAPI = !!target;
      return super.open(method, target || input, ...rest);
    }
    setRequestHeader(name, value) {
      if (this.nativeMusicAPI && name.toLowerCase() === 'authorization') name = 'X-Music-Authorization';
      return super.setRequestHeader(name, value);
    }
  };
  window.fetch = (input, init = {}) => {
    const target = mapped(input instanceof Request ? input.url : input);
    if (!target) return originalFetch(input, init);
    const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
    const auth = headers.get('Authorization');
    if (auth) { headers.delete('Authorization'); headers.set('X-Music-Authorization', auth); }
    return originalFetch(target, {...init, headers});
  };
  for (const Type of [window.HTMLMediaElement, window.HTMLSourceElement, window.HTMLImageElement]) {
    if (!Type) continue;
    const descriptor = Object.getOwnPropertyDescriptor(Type.prototype, 'src');
    if (descriptor?.set) Object.defineProperty(Type.prototype, 'src', {...descriptor,
      set(value) { return descriptor.set.call(this, mapped(value) || value); },
    });
  }

  // Plugin pages need their own correct origin for assets and API requests.
  // The native Flutter client can append /api/... after index.html. Send only
  // its same-origin Songloft plugin frames to this NAS's authenticated gateway
  // endpoint; retain the existing application session for the host auth bridge.
  const iframeType = window.HTMLIFrameElement;
  const iframeSrc = iframeType && Object.getOwnPropertyDescriptor(iframeType.prototype, 'src');
  if (iframeSrc?.set) Object.defineProperty(window.HTMLIFrameElement.prototype, 'src', {
    ...iframeSrc,
    set(value) {
      const url = new URL(value, location.href);
      const marker = url.pathname.indexOf('/api/v1/jsplugin/');
      const target = url.origin === base.origin && marker >= 0
        ? new URL('vendor-ui' + url.pathname.slice(marker), base).href + url.search + url.hash
        : value;
      return iframeSrc.set.call(this, target);
    },
  });
})();
