(() => {
  'use strict';
  const paths = {
    player: ['vendor-ui/index.html', 'Songloft 音乐播放器'],
    search: ['../resources/pansou/vendor-ui/index.html?music=1', '盘搜音乐资源'],
    downloads: ['downloads.html', '音乐下载与入库'],
  };
  const frames = new Map();
  const error = document.getElementById('workflow-error');
  const base = window.__MICRO_APP_PUBLIC_PATH__ || new URL('.', location.href).href;
  function activate(view) {
    if (!paths[view]) return;
    error.hidden = true;
    try {
      if (!frames.has(view)) {
        const url = new URL(paths[view][0], base);
        if (!['http:', 'https:'].includes(url.protocol)) throw Error('原生访问地址不可用，请重新打开音乐。');
        const frame = document.createElement('iframe');
        frame.src = url.href; frame.title = paths[view][1]; frame.allow = 'fullscreen';
        frame.referrerPolicy = 'same-origin';
        document.getElementById('music-frames').append(frame); frames.set(view, frame);
      }
      for (const [key, frame] of frames) frame.hidden = key !== view;
      document.querySelectorAll('[data-view]').forEach(button => {
        button.setAttribute('aria-pressed', String(button.dataset.view === view));
      });
    } catch (e) { error.textContent = e.message; error.hidden = false; }
  }
  document.querySelectorAll('[data-view]').forEach(button => button.onclick = () => activate(button.dataset.view));
  const back = document.getElementById('music-native-back');
  back.hidden = !(window.flutter_inappwebview || window.android_webview || /Android|iPhone|iPad/i.test(navigator.userAgent) || (navigator.maxTouchPoints > 1 && /Macintosh/i.test(navigator.userAgent)));
  back.onclick = () => window.hs_callHandler('normal_goback', {}, response => {
    try { if (JSON.parse(response).code === 0) return; } catch {}
    error.textContent = '返回失败，请重新打开音乐后重试。'; error.hidden = false;
  });
  activate('player');
})();
