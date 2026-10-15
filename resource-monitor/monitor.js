(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const tabs = ['overview', 'cpu', 'memory', 'network', 'disk', 'docker'];
  const state = {
    activeTab: 'overview',
    filter: 'all',
    search: '',
    host: null,
    lastGoodHost: null,
    docker: null,
    lastGoodDocker: null,
    hostInFlight: null,
    dockerInFlight: null,
    hostController: null,
    dockerController: null,
    hostTimer: null,
    dockerTimer: null,
    history: { cpu: [], memory: [], rx: [], tx: [] }
  };

  const baseUrl = typeof window === 'undefined' ? null : (() => {
    try {
      const publicPath = window.__MICRO_APP_PUBLIC_PATH__;
      const url = new URL(publicPath || '.', document.baseURI);
      if (publicPath && !url.pathname.endsWith('/')) url.pathname += '/';
      return url;
    } catch (_) {
      return new URL('.', window.location.href);
    }
  })();

  function endpoint(api) {
    const url = new URL('monitor.cgi', baseUrl);
    url.searchParams.set('api', api);
    return url.href;
  }

  function finite(value) {
    if (typeof value === 'number') return Number.isFinite(value) ? value : null;
    if (typeof value !== 'string' || value.trim() === '') return null;
    const n = Number(value.trim());
    return Number.isFinite(n) ? n : null;
  }

  function positive(value) {
    const n = finite(value);
    return n === null || n < 0 ? null : n;
  }

  function clampPercent(value) {
    const n = finite(value);
    return n === null ? null : Math.max(0, Math.min(100, n));
  }

  function fmtNumber(value, decimals) {
    const n = finite(value);
    return n === null ? '—' : n.toLocaleString('zh-CN', { maximumFractionDigits: decimals });
  }

  function fmtBytes(value) {
    const n = positive(value);
    if (n === null) return '—';
    const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
    let scaled = n;
    let unit = 0;
    while (scaled >= 1000 && unit < units.length - 1) { scaled /= 1000; unit += 1; }
    const decimals = unit === 0 ? 0 : scaled >= 10 ? 1 : 2;
    return `${scaled.toLocaleString('zh-CN', { maximumFractionDigits: decimals })} ${units[unit]}`;
  }

  function fmtRate(value) {
    const n = positive(value);
    return n === null ? '—' : `${fmtBytes(n)}/s`;
  }

  function fmtPercent(value) {
    const n = clampPercent(value);
    return n === null ? '—' : `${fmtNumber(n, 1)}%`;
  }

  function displayText(value, fallback) {
    if (typeof value === 'string' && value.trim()) return value.trim();
    if (typeof value === 'number' && Number.isFinite(value)) return String(value);
    return fallback || '未提供';
  }

  function fmtCoreCount(value) {
    const n = finite(value);
    return n === null ? '未知' : `${fmtNumber(n, 0)} 核`;
  }

  function fmtDuration(value) {
    const seconds = positive(value);
    if (seconds === null) return '—';
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    if (days) return `${days}天 ${hours}小时`;
    if (hours) return `${hours}小时 ${minutes}分`;
    return `${minutes}分`;
  }

  function fmtTime(value) {
    const n = finite(value);
    if (n === null) return '时间未知';
    return new Date(n * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  }

  function text(id, value) {
    const node = $(id);
    if (node) node.textContent = value == null ? '—' : String(value);
  }

  function sumKnown(items, key) {
    let total = 0;
    let hasItem = false;
    let unknown = false;
    (Array.isArray(items) ? items : []).forEach(item => {
      hasItem = true;
      const value = finite(item && item[key]);
      if (value === null) unknown = true;
      else total += Math.max(0, value);
    });
    return hasItem && !unknown ? total : null;
  }

  function warningText(item) {
    if (typeof item === 'string') return item;
    if (item && typeof item === 'object') return String(item.message || item.reason || item.warning || '数据警告');
    return String(item || '数据警告');
  }

  function warningsMarkup(items, emptyText) {
    const list = Array.isArray(items) ? items.map(warningText).filter(Boolean) : [];
    return list.length ? list.map(item => `<div class="row"><div><strong>${escapeHtml(item)}</strong></div></div>`).join('') : `<p class="empty">${escapeHtml(emptyText)}</p>`;
  }

  function setSampleState(dotId, noteId, sampleState, sampledAt, extra) {
    const labels = { ok: '正常', warming: '预热中', reset: '计数器已重置', stale: '数据已过期', unavailable: '不可用' };
    const label = labels[sampleState] || '未知';
    const dot = $(dotId);
    if (dot) dot.dataset.state = sampleState || 'unknown';
    const suffix = sampledAt == null ? '' : ` · ${fmtTime(sampledAt)}`;
    text(noteId, `${label}${suffix}${extra ? ` · ${extra}` : ''}`);
    return label;
  }

  function setGauge(id, value) {
    const node = $(id);
    const n = clampPercent(value);
    if (!node) return;
    node.style.setProperty('--value', n === null ? 0 : n);
  }

  function rateLabel(value, prefix) {
    return `${prefix}${fmtRate(value)}`;
  }

  function pushHistory(kind, sampledAt, value) {
    const at = finite(sampledAt);
    if (at === null) return;
    const list = state.history[kind];
    list.push({ at, value: finite(value) });
    const cutoff = at - 300;
    while (list.length && list[0].at < cutoff) list.shift();
    if (list.length > 65) list.splice(0, list.length - 65);
  }

  function renderSeries(groupId, kind, fixedMax, clearGroup) {
    const group = $(groupId);
    if (!group) return;
    const points = state.history[kind].filter(item => item && finite(item.at) !== null && item.at >= (Date.now() / 1000 - 300));
    if (clearGroup !== false) group.innerHTML = '';
    if (!points.length) return;
    const now = Date.now() / 1000;
    const minAt = Math.max(now - 300, points[0].at);
    const maxAt = Math.max(now, minAt + 1);
    const maxValue = fixedMax || Math.max(1, ...points.map(item => finite(item.value) === null ? 0 : item.value));
    const colorClass = kind === 'rx' ? 'rx' : kind === 'tx' ? 'tx' : kind === 'cpu' ? 'cpu' : 'memory';
    let segment = [];
    const segments = [];
    const addSegment = () => { if (segment.length > 1) segments.push(segment.join(' ')); segment = []; };
    points.forEach(point => {
      const value = finite(point.value);
      if (value === null) { addSegment(); return; }
      const x = Math.max(0, Math.min(600, ((point.at - minAt) / (maxAt - minAt)) * 600));
      const y = 170 - (Math.max(0, value) / maxValue) * 150;
      segment.push(`${x.toFixed(2)},${Math.max(20, Math.min(170, y)).toFixed(2)}`);
    });
    addSegment();
    segments.forEach(pointsAttr => {
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
      path.setAttribute('points', pointsAttr);
      path.setAttribute('class', `series ${colorClass}`);
      path.setAttribute('aria-hidden', 'true');
      group.appendChild(path);
    });
  }

  function renderCharts() {
    renderSeries('cpu-series', 'cpu', 100);
    renderSeries('memory-series', 'memory', 100);
    const networkPoints = state.history.rx.concat(state.history.tx).map(item => finite(item.value)).filter(value => value !== null);
    const max = networkPoints.length ? Math.max(1, ...networkPoints) : null;
    renderSeries('network-series', 'rx', max, true);
    renderSeries('network-series', 'tx', max, false);
    text('network-chart-max', max === null ? '—' : fmtRate(max));
    text('network-chart-unit', max === null ? '—' : `纵轴 ${fmtRate(max)}`);
  }

  function renderVolumes(volumes, targetId) {
    const rows = Array.isArray(volumes) ? volumes : [];
    $(targetId).innerHTML = rows.length ? rows.map(volume => {
      const used = fmtPercent(volume.used_percent);
      const total = fmtBytes(volume.total);
      const free = fmtBytes(volume.free);
      return `<div class="row"><div><strong>${escapeHtml(volume.mount || '挂载点未知')}</strong><span>${escapeHtml(volume.source || '设备未知')} · ${escapeHtml(volume.fs || '文件系统未知')}</span></div><div class="row-end"><strong>${escapeHtml(free)} 可用</strong><span>${escapeHtml(total)} 总量 · ${escapeHtml(used)} 已用</span></div></div>`;
    }).join('') : '<p class="empty">未发现可读取的已挂载存储卷</p>';
  }

  function renderTemperatures(temperatures, targetId) {
    const rows = Array.isArray(temperatures) ? temperatures : [];
    $(targetId).innerHTML = rows.length ? rows.map(item => `<div class="row"><div><strong>${escapeHtml(item.name || '传感器')}</strong><span>系统提供的温度读数</span></div><div class="row-end"><strong>${escapeHtml(fmtNumber(item.celsius, 1))} °C</strong></div></div>`).join('') : '<p class="empty">设备未提供可读取的温度传感器</p>';
  }

  function renderNetworks(network) {
    const rows = Array.isArray(network) ? network : [];
    $('network-rows').innerHTML = rows.length ? rows.map(item => `<div class="row"><div><strong>${escapeHtml(item.name || '接口未知')}</strong><span>累计接收 ${escapeHtml(fmtBytes(item.rx))} · 累计发送 ${escapeHtml(fmtBytes(item.tx))}</span></div><div class="row-end"><strong>↓ ${escapeHtml(fmtRate(item.rx_rate))}</strong><span>↑ ${escapeHtml(fmtRate(item.tx_rate))}</span></div></div>`).join('') : '<p class="empty">没有可见的网络接口</p>';
  }

  function renderDiskIo(diskIo) {
    const entries = diskIo && typeof diskIo === 'object' ? Object.entries(diskIo) : [];
    $('disk-io').innerHTML = entries.length ? entries.map(([name, item]) => `<div class="row"><div><strong>${escapeHtml(name)}</strong><span>设备采样速率</span></div><div class="row-end"><strong>读 ${escapeHtml(fmtRate(item && item.read_rate))}</strong><span>写 ${escapeHtml(fmtRate(item && item.write_rate))}</span></div></div>`).join('') : '<p class="empty">没有可见的本地磁盘计数</p>';
  }

  function renderHost(data) {
    const raw = data && typeof data === 'object' ? data : {};
    const sampledAt = finite(raw.sampled_at);
    const sampleState = String(raw.sample_state || 'ok');
    if (!['stale', 'unavailable'].includes(sampleState)) state.lastGoodHost = raw;
    const view = ['stale', 'unavailable'].includes(sampleState) && state.lastGoodHost ? state.lastGoodHost : raw;
    state.host = view;
    data = view;
    const memory = data && data.memory && typeof data.memory === 'object' ? data.memory : {};
    const cpuInfo = data && data.cpu && typeof data.cpu === 'object' ? data.cpu : {};
    const cpu = clampPercent(data && data.cpu_percent !== undefined ? data.cpu_percent : cpuInfo.percent);
    const nestedCores = finite(cpuInfo.cores);
    const coreCount = nestedCores === null ? finite(data && data.cpu_cores) : nestedCores;
    const system = data && data.system && typeof data.system === 'object' ? data.system : {};
    const memoryPercent = clampPercent(memory.percent);
    const network = Array.isArray(data && data.network) ? data.network : [];
    const diskIo = data && data.disk_io && typeof data.disk_io === 'object' ? data.disk_io : {};
    const temperatures = Array.isArray(data && data.temperatures) ? data.temperatures : [];
    const rx = sumKnown(network, 'rx_rate');
    const tx = sumKnown(network, 'tx_rate');
    const diskEntries = Object.values(diskIo);
    const read = sumKnown(diskEntries, 'read_rate');
    const write = sumKnown(diskEntries, 'write_rate');
    const loads = Array.isArray(data && data.load_average) ? data.load_average.slice(0, 3) : [];

    text('overview-cpu', fmtPercent(cpu));
    text('cpu-current', fmtPercent(cpu));
    text('overview-memory', fmtPercent(memoryPercent));
    text('overview-rx', rateLabel(rx, '↓ '));
    text('overview-tx', rateLabel(tx, '↑ '));
    text('overview-read', rateLabel(read, '读 '));
    text('overview-write', rateLabel(write, '写 '));
    text('overview-uptime', fmtDuration(data && data.uptime_seconds));
    text('overview-load', loads.length ? loads.join(' / ') : '—');
    text('overview-cores', fmtCoreCount(coreCount));
    text('overview-available', fmtBytes(memory.available));
    text('cpu-cores', fmtCoreCount(coreCount));
    text('cpu-load', loads.length ? loads.join(' / ') : '—');
    text('cpu-load-detail', loads.length ? loads.join(' / ') : '—');
    text('cpu-temperature', temperatures.length && finite(temperatures[0].celsius) !== null ? `${fmtNumber(temperatures[0].celsius, 1)} °C` : '—');
    text('memory-used-main', memory.used === undefined ? '—' : `${fmtBytes(memory.used)} / ${fmtBytes(memory.total)}`);
    text('memory-total', fmtBytes(memory.total));
    text('memory-available', fmtBytes(memory.available));
    text('memory-percent', `使用率 ${fmtPercent(memoryPercent)}`);
    text('memory-free', fmtBytes(memory.free));
    text('memory-buffers', fmtBytes(memory.buffers));
    text('memory-cached', fmtBytes(memory.cached));
    text('memory-swap', memory.swap_total === undefined && memory.swap_used === undefined ? '—' : `${fmtBytes(memory.swap_used)} / ${fmtBytes(memory.swap_total)}`);
    text('system-model', displayText(system.model, '未知'));
    text('system-cpu-model', displayText(system.cpu_model, '未知'));
    text('system-cpu-cores', `核心数：${fmtCoreCount(coreCount)}`);
    text('system-memory-total', memory.total === null || memory.total === undefined ? '未提供' : fmtBytes(memory.total));
    text('system-firmware', displayText(system.firmware_version, '未提供'));
    const osName = displayText(system.os_name, '系统未知');
    const osVersion = displayText(system.os_version, '版本未知');
    text('system-os', `${osName} ${osVersion}`);
    text('system-architecture', `架构：${displayText(system.architecture, '未提供')}`);
    text('system-kernel', displayText(system.kernel, '未提供'));
    setGauge('cpu-gauge', cpu);
    setGauge('memory-gauge', memoryPercent);
    $('cpu-meter').style.width = `${cpu === null ? 0 : cpu}%`;
    $('memory-meter').style.width = `${memoryPercent === null ? 0 : memoryPercent}%`;
    if (temperatures.length && finite(temperatures[0].celsius) !== null) {
      text('overview-temperature', `${fmtNumber(temperatures[0].celsius, 1)} °C`);
      text('overview-temperature-name', temperatures[0].name || '系统传感器');
    } else {
      text('overview-temperature', '—');
      text('overview-temperature-name', '没有可用传感器');
    }
    renderVolumes(data && data.volumes, 'overview-volumes');
    renderVolumes(data && data.volumes, 'disk-volumes');
    renderTemperatures(temperatures, 'disk-temperatures');
    renderNetworks(network);
    renderDiskIo(diskIo);
    const hostWarnings = Array.isArray(data && data.warnings) ? data.warnings : [];
    $('overview-warnings').innerHTML = warningsMarkup(hostWarnings, '暂无警告');
    const label = setSampleState('host-sample-dot', 'host-sample-note', sampleState, sampledAt, hostWarnings.length ? `${hostWarnings.length} 条警告` : '');
    const hasStateProblem = ['stale', 'unavailable'].includes(sampleState);
    $('header-state').dataset.tone = hasStateProblem ? 'error' : sampleState === 'ok' ? 'ok' : '';
    text('header-state', label);
    const lastGoodAt = finite(state.lastGoodHost && state.lastGoodHost.sampled_at);
    text('last-good', lastGoodAt === null ? '最近成功采样时间未知' : `主机数据 ${fmtTime(lastGoodAt)}`);
    if (sampledAt !== null && !['stale', 'unavailable'].includes(sampleState)) {
      pushHistory('cpu', sampledAt, cpu);
      pushHistory('memory', sampledAt, memoryPercent);
      pushHistory('rx', sampledAt, rx);
      pushHistory('tx', sampledAt, tx);
      renderCharts();
    }
    if (!hasStateProblem) {
      $('error-strip').hidden = true;
      $('status').dataset.tone = sampleState === 'ok' ? 'ok' : '';
      text('status', sampleState === 'ok' ? `主机更新于 ${fmtTime(sampledAt)}` : `主机数据${label} · ${fmtTime(sampledAt)}`);
    } else {
      $('error-strip').hidden = false;
      $('error-strip').textContent = `主机数据状态：${label}。页面保留最近一次成功读数。`;
      $('status').dataset.tone = 'error';
      text('status', `主机数据${label}`);
    }
  }

  function renderDocker(data) {
    const raw = data && typeof data === 'object' ? data : {};
    const sampledAt = finite(raw.sampled_at);
    const rawAvailable = raw.available === true;
    const sampleState = String(raw.sample_state || (rawAvailable ? 'ok' : 'unavailable'));
    if (rawAvailable && !['stale', 'unavailable'].includes(sampleState)) state.lastGoodDocker = raw;
    const view = ['stale', 'unavailable'].includes(sampleState) && state.lastGoodDocker ? state.lastGoodDocker : raw;
    state.docker = view;
    data = view;
    const available = data.available === true;
    const containers = available && Array.isArray(data.containers) ? data.containers : [];
    const projects = available && Array.isArray(data.projects) ? data.projects : [];
    const projectCount = finite(data && data.project_count) === null ? projects.length : finite(data.project_count);
    $('docker-summary').innerHTML = available ? `<div class="metric"><span>运行中</span><strong>${escapeHtml(fmtNumber(data.running, 0))}</strong></div><div class="metric"><span>已停止</span><strong>${escapeHtml(fmtNumber(data.stopped, 0))}</strong></div><div class="metric"><span>全部容器</span><strong>${escapeHtml(fmtNumber(data.total, 0))}</strong></div><div class="metric"><span>Compose 项目</span><strong>${escapeHtml(fmtNumber(projectCount, 0))}</strong></div>` : '<div class="metric"><span>Docker 状态</span><strong>不可用</strong></div>';
    $('docker-projects').innerHTML = projects.length ? projects.map(project => `<div class="row"><div><strong>${escapeHtml(project.name || '未命名项目')}</strong><span>Compose 项目</span></div><div class="row-end"><strong>${escapeHtml(fmtNumber(project.running, 0))} 运行中</strong><span>${escapeHtml(fmtNumber(project.stopped, 0))} 已停止 · ${escapeHtml(fmtNumber(project.total, 0))} 全部</span></div></div>`).join('') : '';
    const dockerWarnings = Array.isArray(data && data.warnings) ? data.warnings : [];
    $('docker-warnings').innerHTML = available ? warningsMarkup(dockerWarnings, data && data.stats_available === false ? '容器状态可用，资源统计暂不可用' : '暂无警告') : warningsMarkup([data && data.reason ? data.reason : 'Docker 只读状态不可用'], 'Docker 不可用');
    const statsNote = available && data.stats_available === false ? '资源统计暂不可用 · 只显示状态' : available ? '资源统计与状态均为只读' : '不会请求 Docker 管理权限';
    text('docker-list-note', statsNote);
    setSampleState('docker-sample-dot', 'docker-sample-note', sampleState, sampledAt, data && data.reason ? data.reason : '');
    if (available) renderContainerList(containers);
    else $('docker-list').innerHTML = '<p class="empty">系统监控账号没有 Docker 只读访问权限。</p>';
  }

  function isRunning(item) {
    return String(item && item.state || '').toLowerCase() === 'running';
  }

  function renderContainerList(containers) {
    const needle = state.search.trim().toLowerCase();
    const visible = containers.filter(item => {
      const matchesFilter = state.filter === 'all' || (state.filter === 'running' ? isRunning(item) : !isRunning(item));
      const haystack = [item.name, item.image, item.project, item.status, item.id].map(value => String(value || '').toLowerCase()).join(' ');
      return matchesFilter && (!needle || haystack.includes(needle));
    });
    if (!visible.length) {
      $('docker-list').innerHTML = '<p class="empty">当前筛选条件下没有容器</p>';
      return;
    }
    $('docker-list').innerHTML = visible.map(item => {
      const stats = item.stats && typeof item.stats === 'object' ? item.stats : {};
      const running = isRunning(item);
      const stateLabel = running ? '运行中' : (item.state || '已停止');
      const network = stats.network == null || stats.network === '' ? '—' : String(stats.network);
      const blockIo = stats.block_io == null || stats.block_io === '' ? '—' : String(stats.block_io);
      return `<article class="container-card"><div class="container-main"><div class="container-name"><strong>${escapeHtml(item.name || '未命名容器')}</strong><div class="container-state"><i class="state-dot${running ? ' running' : ''}" aria-hidden="true"></i>${escapeHtml(stateLabel)} · ${escapeHtml(item.status || '状态未知')}</div><div class="container-project">项目：${escapeHtml(item.project || '无 Compose 项目')}</div></div></div><div class="container-stats"><div><strong>${escapeHtml(stats.cpu == null ? '—' : stats.cpu)}</strong><span>CPU</span></div><div><strong>${escapeHtml(stats.memory == null ? '—' : stats.memory)}</strong><span>内存</span></div><div><strong>收/发 ${escapeHtml(network)}</strong><span>网络累计</span></div></div><details class="container-details"><summary>查看镜像、ID、端口与累计 I/O</summary><dl class="detail-list"><div><dt>镜像</dt><dd>${escapeHtml(item.image || '—')}</dd></div><div><dt>容器 ID</dt><dd>${escapeHtml(item.id || '—')}</dd></div><div class="full"><dt>端口</dt><dd>${escapeHtml(item.ports || '—')}</dd></div><div><dt>网络（收/发累计）</dt><dd>${escapeHtml(network)}</dd></div><div><dt>块设备（读/写累计）</dt><dd>${escapeHtml(blockIo)}</dd></div><div><dt>内存使用率</dt><dd>${escapeHtml(stats.memory_percent == null ? '—' : stats.memory_percent)}</dd></div></dl></details></article>`;
    }).join('');
  }

  function setTab(tab, push) {
    const next = tabs.includes(tab) ? tab : 'overview';
    state.activeTab = next;
    document.querySelectorAll('[role="tab"]').forEach(button => {
      const selected = button.dataset.tab === next;
      button.setAttribute('aria-selected', String(selected));
      button.tabIndex = selected ? 0 : -1;
    });
    document.querySelectorAll('[data-panel]').forEach(panel => {
      const active = panel.dataset.panel === next;
      panel.hidden = !active;
    });
    if (push && window.location.hash !== `#${next}`) window.history.pushState({ tab: next }, '', `#${next}`);
  }

  function handleTabs(event) {
    const button = event.target.closest('[role="tab"]');
    if (!button) return;
    setTab(button.dataset.tab, true);
    button.focus();
  }

  function handleTabKey(event) {
    const button = event.target.closest('[role="tab"]');
    if (!button) return;
    const index = tabs.indexOf(button.dataset.tab);
    let next = null;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = tabs[(index + 1) % tabs.length];
    if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = tabs[(index - 1 + tabs.length) % tabs.length];
    if (event.key === 'Home') next = tabs[0];
    if (event.key === 'End') next = tabs[tabs.length - 1];
    if (!next) return;
    event.preventDefault();
    setTab(next, true);
    $(`tab-${next}`).focus();
  }

  function handleBack() {
    const hint = $('status');
    text('status', '正在返回…');
    if (typeof window.hs_callHandler !== 'function') {
      text('status', '原生返回不可用');
      return;
    }
    window.hs_callHandler('normal_goback', {}, response => {
      try {
        const result = typeof response === 'string' ? JSON.parse(response) : response;
        if (!result || result.code !== 0) throw new Error();
      } catch (_) {
        hint.dataset.tone = 'error';
        text('status', '返回失败，请重新打开插件后重试');
      }
    });
  }

  async function fetchJson(api, timeout, controller) {
    const timer = window.setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(endpoint(api), { cache: 'no-store', signal: controller.signal, headers: { Accept: 'application/json' } });
      if (!response.ok) throw new Error(`${api === 'docker' ? 'Docker' : '主机'}状态读取失败（${response.status}）`);
      return await response.json();
    } finally {
      window.clearTimeout(timer);
    }
  }

  async function refreshHost() {
    if (state.hostInFlight || document.hidden) return;
    state.hostController = new AbortController();
    state.hostInFlight = fetchJson('status', 8000, state.hostController);
    try {
      renderHost(await state.hostInFlight);
    } catch (error) {
      $('error-strip').hidden = false;
      $('error-strip').textContent = `主机状态读取失败：${error.name === 'AbortError' ? '请求超时' : error.message}。页面保留最近一次成功读数。`;
      $('status').dataset.tone = 'error';
      text('status', '主机读取失败 · 保留上次数据');
      $('header-state').dataset.tone = 'error';
      text('header-state', '读取失败');
    } finally {
      state.hostInFlight = null;
      state.hostController = null;
    }
  }

  async function refreshDocker() {
    if (state.dockerInFlight || document.hidden) return;
    state.dockerController = new AbortController();
    state.dockerInFlight = fetchJson('docker', 12000, state.dockerController);
    try {
      renderDocker(await state.dockerInFlight);
    } catch (error) {
      const last = finite(state.docker && state.docker.sampled_at);
      const lastText = last === null ? '尚无成功采样' : `最近成功 ${fmtTime(last)}`;
      text('docker-sample-note', `读取失败 · ${error.name === 'AbortError' ? '请求超时' : error.message} · ${lastText}`);
      $('docker-sample-dot').dataset.state = 'stale';
      $('docker-warnings').innerHTML = warningsMarkup(['Docker 状态读取失败，页面保留上次成功列表。'], '暂无警告');
    } finally {
      state.dockerInFlight = null;
      state.dockerController = null;
    }
  }

  function stopPolling(abortRequests) {
    if (state.hostTimer !== null) window.clearInterval(state.hostTimer);
    if (state.dockerTimer !== null) window.clearInterval(state.dockerTimer);
    state.hostTimer = null;
    state.dockerTimer = null;
    if (abortRequests) {
      if (state.hostController) state.hostController.abort();
      if (state.dockerController) state.dockerController.abort();
    }
  }

  function startPolling() {
    stopPolling(false);
    if (document.hidden) return;
    const pending = [state.hostInFlight, state.dockerInFlight].filter(Boolean);
    if (pending.length) {
      Promise.allSettled(pending).then(() => { if (!document.hidden) startPolling(); });
      return;
    }
    refreshHost();
    refreshDocker();
    state.hostTimer = window.setInterval(refreshHost, 5000);
    state.dockerTimer = window.setInterval(refreshDocker, 15000);
  }

  function setup() {
    const hashTab = window.location.hash.slice(1).toLowerCase();
    setTab(tabs.includes(hashTab) ? hashTab : 'overview', false);
    $('tabs').addEventListener('click', handleTabs);
    $('tabs').addEventListener('keydown', handleTabKey);
    window.addEventListener('hashchange', () => setTab(window.location.hash.slice(1).toLowerCase(), false));
    window.addEventListener('popstate', () => setTab(window.location.hash.slice(1).toLowerCase(), false));
    $('native-back').addEventListener('click', handleBack);
    document.querySelectorAll('[data-filter]').forEach(button => button.addEventListener('click', () => {
      state.filter = button.dataset.filter;
      document.querySelectorAll('[data-filter]').forEach(item => {
        const active = item === button;
        item.classList.toggle('active', active);
        item.setAttribute('aria-pressed', String(active));
      });
      if (state.docker && state.docker.available) renderContainerList(state.docker.containers || []);
    }));
    $('docker-search').addEventListener('input', event => {
      state.search = event.target.value;
      if (state.docker && state.docker.available) renderContainerList(state.docker.containers || []);
    });
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) stopPolling(true);
      else startPolling();
    });
    startPolling();
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { finite, positive, clampPercent, fmtNumber, fmtBytes, fmtRate, fmtPercent, fmtDuration, displayText, fmtCoreCount, sumKnown };
    return;
  }
  setup();
})();
