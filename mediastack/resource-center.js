(() => {
  'use strict';

  const tabs = {
    search: { kicker: '搜索', title: '盘搜资源', mode: '网盘与磁力', description: '搜索网盘分享资源。结果来自第三方来源，请在打开前核对内容。', path: '../pansou/vendor-ui/index.html' },
    pt: { kicker: 'PT 下载', title: 'PT 下载器', mode: '直连 · 独立登录', description: '管理 PT 下载任务。此入口保留独立的账号、保存路径、限速和任务状态。', path: '../pt/index.html?embedded=1' },
    public: { kicker: '公共下载', title: '公共下载器', mode: '代理 · 独立登录', description: '管理公共下载任务。此入口保留独立的账号、保存路径、限速和任务状态。', path: '../public/index.html?embedded=1' }
  };

  const frames = new Map();
  const timers = new Map();
  const loaded = new Set();
  const failed = new Set();
  const gatewayErrors = [
    'Unknown application route',
    'Application temporarily unavailable',
    'Native owner login required',
    'Unsupported request'
  ];
  const frameHost = document.getElementById('frame-host');
  const frameState = document.getElementById('frame-state');
  const frameStateText = document.getElementById('frame-state-text');
  const sectionKicker = document.getElementById('section-kicker');
  const sectionTitle = document.getElementById('section-title');
  const sectionMode = document.getElementById('endpoint-mode');
  const sectionDescription = document.getElementById('section-description');

  function centerDirectory() {
    // Resolve from the real center document URL. This handles a hash and
    // micro-app paths such as /D:/plugin/.../center without nested mistakes.
    const current = new URL(window.location.href);
    current.hash = '';
    current.search = '';
    const hintedPath = window.__MICRO_APP_PUBLIC_PATH__;
    if (typeof hintedPath === 'string' && hintedPath) {
      try {
        const hinted = new URL(hintedPath, current.href);
        if (hinted.origin === current.origin && /\/center\/?$/.test(hinted.pathname)) {
          hinted.pathname = hinted.pathname.replace(/\/?$/, '/');
          hinted.hash = '';
          hinted.search = '';
          return hinted;
        }
      } catch { /* The real document URL remains the safe fallback. */ }
    }
    return new URL('.', current.href);
  }

  function fixedRoute(path) {
    const url = new URL(path, centerDirectory());
    if (url.origin !== window.location.origin ||
        !(url.pathname.includes('/pt/') || url.pathname.includes('/public/') || url.pathname.includes('/pansou/'))) {
      throw new Error('固定资源入口不可用');
    }
    return url.href;
  }

  function setState(text, error = false) {
    frameState.hidden = false;
    frameState.dataset.tone = error ? 'error' : 'loading';
    frameState.querySelector('.spinner').hidden = error;
    frameStateText.textContent = text;
    let retry = frameState.querySelector('.retry');
    if (error) {
      if (!retry) {
        retry = document.createElement('button');
        retry.type = 'button';
        retry.className = 'retry';
        retry.textContent = '重新载入';
        retry.addEventListener('click', () => activate(activeTab, true));
        frameState.append(retry);
      }
    } else if (retry) retry.remove();
  }

  function showFrame(frame, id) {
    for (const [key, item] of frames) {
      const selected = key === id;
      item.hidden = !selected;
      item.setAttribute('aria-hidden', String(!selected));
    }
    frame.hidden = false;
  }

  function gatewayError(frame) {
    try {
      const marker = frame.contentDocument?.querySelector('meta[name="mediastack-error"]');
      if (marker) return `HTTP ${marker.getAttribute('content') || 'error'}`;
      const text = frame.contentDocument?.body?.textContent || '';
      return gatewayErrors.find(message => text.includes(message)) || '';
    } catch {
      return '';
    }
  }

  function makeFrame(id) {
    const frame = document.createElement('iframe');
    frame.title = tabs[id].title;
    frame.loading = 'lazy';
    frame.allow = 'fullscreen';
    frame.referrerPolicy = 'same-origin';
    frame.addEventListener('load', () => {
      clearTimeout(timers.get(id));
      timers.delete(id);
      const error = gatewayError(frame);
      if (error) {
        loaded.delete(id);
        failed.add(id);
        if (activeTab === id) setState('应用暂时无法打开，请重试。', true);
        return;
      }
      failed.delete(id);
      loaded.add(id);
      if (activeTab === id) frameState.hidden = true;
    });
    frame.addEventListener('error', () => {
      clearTimeout(timers.get(id));
      timers.delete(id);
      loaded.delete(id);
      failed.add(id);
      if (activeTab === id) setState('应用暂时无法打开，请稍后重试。', true);
    });
    frame.src = fixedRoute(tabs[id].path);
    frameHost.append(frame);
    frames.set(id, frame);
    timers.set(id, setTimeout(() => {
      if (activeTab === id && !frameState.hidden) setState('载入超时，请检查应用状态后重试。', true);
    }, 15000));
    return frame;
  }

  let activeTab = 'search';
  function activate(id, retry = false) {
    if (!tabs[id]) return;
    activeTab = id;
    const config = tabs[id];
    sectionKicker.textContent = config.kicker;
    sectionTitle.textContent = config.title;
    sectionMode.textContent = config.mode;
    sectionDescription.textContent = config.description;
    document.querySelectorAll('[data-tab]').forEach(button => {
      const selected = button.dataset.tab === id;
      button.classList.toggle('is-active', selected);
      button.setAttribute('aria-selected', String(selected));
    });
    if (retry && frames.has(id)) {
      const old = frames.get(id);
      clearTimeout(timers.get(id));
      old.remove();
      frames.delete(id);
      loaded.delete(id);
      failed.delete(id);
    }
    setState('正在载入…');
    const frame = frames.get(id) || makeFrame(id);
    showFrame(frame, id);
    if (failed.has(id)) setState('应用暂时无法打开，请重试。', true);
    else if (loaded.has(id)) frameState.hidden = true;
  }

  document.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', () => activate(button.dataset.tab)));

  document.getElementById('native-back').addEventListener('click', () => {
    const button = document.getElementById('native-back');
    button.disabled = true;
    const done = response => {
      button.disabled = false;
      try {
        const result = typeof response === 'string' ? JSON.parse(response) : response;
        if (result?.code !== 0) throw new Error();
      } catch { button.title = '返回失败，请重新打开插件后重试'; }
    };
    if (typeof window.hs_callHandler !== 'function') { done({code: -1}); return; }
    window.hs_callHandler('normal_goback', {}, done);
  });

  try {
    activate((location.hash || '#search').slice(1).split('?')[0] || 'search');
  } catch { setState('资源入口不可用，请重新打开插件后重试。', true); }
})();
