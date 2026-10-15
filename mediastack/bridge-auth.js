// Scope this adapter to the MoviePilot micro-app window. Xiaomi reserves the
// Authorization header for native login; carry the app's separate Bearer token
// in a separate header. The owner-checked CGI restores it only for MoviePilot.
(() => {
  const OriginalXHR = window.XMLHttpRequest;
  window.XMLHttpRequest = class extends OriginalXHR {
    setRequestHeader(name, value) {
      if (name.toLowerCase() === 'authorization' && String(value).startsWith('Bearer '))
        name = 'X-Application-Authorization';
      return super.setRequestHeader(name, value);
    }
  };
  const originalFetch = window.fetch.bind(window);
  window.fetch = (input, init = {}) => {
    const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
    const auth = headers.get('Authorization');
    if (auth?.startsWith('Bearer ')) {
      headers.set('X-Application-Authorization', auth);
      headers.delete('Authorization');
    }
    return originalFetch(input, { ...init, headers });
  };
})();
