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

/* One opt-in navigation control per outer plugin shell, never per iframe. */
(function () {
  function install() {
    if (!document.querySelector('[data-xiaomi-shell]') || document.getElementById('xiaomi-floating-back')) return;
    const style = document.createElement('style');
    style.textContent = `
      [data-xiaomi-shell] #native-back { display:none!important; }
      #xiaomi-floating-back { position:fixed; z-index:2147483000; width:46px; height:46px;
        display:grid; place-items:center; margin:0; padding:0; border:1px solid #ffffff55;
        border-radius:50%; color:#fff; background:#263448d9; box-shadow:0 3px 14px #0003;
        backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px); cursor:pointer;
        touch-action:none; user-select:none; -webkit-user-select:none; -webkit-tap-highlight-color:transparent;
        opacity:.82; transition:opacity .15s; }
      #xiaomi-floating-back:hover,#xiaomi-floating-back:focus-visible { opacity:1; }
      #xiaomi-floating-back:focus-visible { outline:3px solid #7bb9ff; outline-offset:3px; }
      #xiaomi-floating-back:disabled { opacity:.5; }
      #xiaomi-floating-back svg { width:23px; height:23px; pointer-events:none; }
      #xiaomi-floating-hint { position:fixed; z-index:2147483001; left:16px; right:16px;
        bottom:calc(144px + env(safe-area-inset-bottom)); max-width:340px; margin:auto;
        padding:10px 13px; border-radius:10px; background:#263448f2; color:white;
        font:13px/1.5 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif; text-align:center; }
      #xiaomi-floating-hint[hidden] { display:none!important; }
    `;
    document.head.append(style);
    const button = document.createElement('button');
    button.id = 'xiaomi-floating-back';
    button.type = 'button';
    button.title = '返回小米 · 可拖动';
    button.setAttribute('aria-label', '返回小米智能存储，可拖动');
    button.innerHTML = '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="m10 5-7 7 7 7M4 12h13a4 4 0 0 1 4 4v3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    const hint = document.createElement('div');
    hint.id = 'xiaomi-floating-hint';
    hint.setAttribute('role', 'status');
    hint.hidden = true;
    document.body.append(button, hint);
    let side = 'right';
    let ratio = .58;
    let drag = null;
    let suppressClickUntil = 0;
    let hintTimer;
    try {
      const saved = JSON.parse(sessionStorage.getItem('xiaomi-floating-position') || 'null');
      if (saved && ['left','right'].includes(saved.side) && Number.isFinite(saved.ratio)) {
        side = saved.side; ratio = Math.max(0, Math.min(1, saved.ratio));
      }
    } catch (_) {}
    function bounds() {
      const view = window.visualViewport;
      const width = view ? view.width : innerWidth;
      const height = view ? view.height : innerHeight;
      return { minX:10, maxX:Math.max(10,width-56), minY:70, maxY:Math.max(70,height-160) };
    }
    function place() {
      const b = bounds();
      button.style.left = (side === 'left' ? b.minX : b.maxX) + 'px';
      button.style.top = (b.minY + ratio * (b.maxY-b.minY)) + 'px';
    }
    function message(text) {
      clearTimeout(hintTimer); hint.textContent = text; hint.hidden = false;
      hintTimer = setTimeout(() => { hint.hidden = true; }, 3500);
    }
    button.addEventListener('pointerdown', event => {
      if (event.button !== 0 || button.disabled) return;
      drag = {x:event.clientX, y:event.clientY, left:parseFloat(button.style.left), top:parseFloat(button.style.top), moved:false};
      button.setPointerCapture(event.pointerId);
    });
    button.addEventListener('pointermove', event => {
      if (!drag) return;
      const dx=event.clientX-drag.x, dy=event.clientY-drag.y;
      if (Math.hypot(dx,dy)>6) drag.moved=true;
      if (!drag.moved) return;
      const b=bounds();
      button.style.left=Math.min(b.maxX,Math.max(b.minX,drag.left+dx))+'px';
      button.style.top=Math.min(b.maxY,Math.max(b.minY,drag.top+dy))+'px';
    });
    function endDrag(event) {
      if (!drag) return;
      if (drag.moved) {
        const b=bounds();
        side=parseFloat(button.style.left)<(b.minX+b.maxX)/2?'left':'right';
        ratio=(parseFloat(button.style.top)-b.minY)/Math.max(1,b.maxY-b.minY);
        suppressClickUntil=Date.now()+400;
        try { sessionStorage.setItem('xiaomi-floating-position',JSON.stringify({side,ratio})); } catch (_) {}
      }
      drag=null;
      if (button.hasPointerCapture(event.pointerId)) button.releasePointerCapture(event.pointerId);
      place();
    }
    button.addEventListener('pointerup',endDrag);
    button.addEventListener('pointercancel',endDrag);
    button.addEventListener('click', () => {
      if (Date.now()<suppressClickUntil || button.disabled) return;
      if (!window.flutter_inappwebview && !window.android_webview) {
        message('请在小米智能存储客户端内使用返回。'); return;
      }
      button.disabled=true;
      let completed=false;
      const finish=response=>{
        if(completed)return;
        completed=true; clearTimeout(timeout); button.disabled=false;
        try {
          const result=typeof response==='string'?JSON.parse(response):response;
          if (!result || result.code!==0) throw new Error();
        } catch (_) { message('暂时无法返回，请使用客户端关闭按钮后重新打开。'); }
      };
      const timeout=setTimeout(()=>finish({code:-1}),5000);
      window.hs_callHandler('normal_goback',{},finish);
    });
    window.addEventListener('resize',place);
    if (window.visualViewport) window.visualViewport.addEventListener('resize',place);
    place();
  }
  if (document.readyState==='loading') document.addEventListener('DOMContentLoaded',install,{once:true});
  else install();
})();
