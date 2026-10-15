/*
 * Small adapter for the Xiaomi NAS native WebView bridge contract used by
 * these plugins. This is project-authored compatibility code; it is not a
 * copy of Xiaomi's application source.
 */
(function () {
  const callbacks = new Map();
  let requestId = 0;

  function host() {
    return window.flutter_inappwebview || window.android_webview || null;
  }

  function complete(id, response) {
    const callback = callbacks.get(String(id));
    if (!callback) return;
    callbacks.delete(String(id));
    callback(response);
  }

  window.hs_APPtoWebMethodMap = window.hs_APPtoWebMethodMap || new Map();
  window.hs_RequestIdMap = window.hs_RequestIdMap || callbacks;
  window.hs_appCallBackToWeb = complete;
  window.hs_registerHandler = function (name, handler) {
    window.hs_APPtoWebMethodMap.set(name, handler);
  };
  window.hs_AppCallWeb = function (name, params, id) {
    const handler = window.hs_APPtoWebMethodMap.get(name);
    const webview = host();
    if (!handler) return;
    handler(params, response => {
      if (webview && typeof webview.callHandler === "function") {
        webview.callHandler("hs_callBackId", id, response);
      }
    });
  };
  window.hs_callHandler = function (name, data, callback) {
    const id = String(++requestId);
    const invoke = webview => {
      callbacks.set(id, callback || function () {});
      const payload = webview === window.android_webview ? JSON.stringify(data) : data;
      let result;
      try {
        if (typeof webview.callHandler === "function") {
          result = webview.callHandler("hs_webCallAppHandler", name, payload, id);
        } else if (typeof webview._callHandler === "function") {
          const nativeId = Date.now() + Math.floor(Math.random() * 1000);
          result = new Promise(resolve => { webview[nativeId] = resolve; });
          webview._callHandler("hs_webCallAppHandler", nativeId, JSON.stringify([name, payload, id]));
        } else {
          throw new Error("Unsupported native WebView bridge");
        }
      } catch (_) {
        callbacks.delete(id);
        if (callback) callback(JSON.stringify({ code: -9999, msg: "Native bridge call failed" }));
        return;
      }
      if (result && typeof result.then === "function") {
        result.then(code => {
          if (code !== 0) complete(id, JSON.stringify({ code, msg: "Native method call failed" }));
        }).catch(() => complete(id, JSON.stringify({ code: -9999, msg: "Native bridge call failed" })));
      }
    };

    let webview = host();
    if (webview) return invoke(webview);
    let attempts = 0;
    const timer = setInterval(() => {
      webview = host();
      if (webview) {
        clearInterval(timer);
        invoke(webview);
      } else if (++attempts >= 60) {
        clearInterval(timer);
        if (callback) callback(JSON.stringify({ code: -9999, msg: "Native bridge unavailable" }));
      }
    }, 50);
  };
})();
