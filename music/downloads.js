(() => {
  'use strict';
  const endpoint = new URL('music.cgi?api=downloads', location.href);
  const $ = id => document.getElementById(id);
  let busy = false;
  const labels = {pending:'等待下载',downloading:'下载中',stabilizing:'正在核对文件',imported:'已入库',error:'需要处理',copying:'正在入库',queued:'等待下载'};
  function say(message, error = false) { $('download-message').textContent = message; $('download-message').dataset.error = String(error); }
  async function request(url, body) {
    const response = await fetch(url, {cache:'no-store', ...(body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : {})});
    const data = await response.json();
    if (!response.ok) throw Error(data.message || data.error || '音乐下载暂时不可用');
    return data;
  }
  async function refresh() {
    try {
      const data = await request(endpoint);
      if (data.error) throw Error(data.error.message || '公共下载器暂时不可用');
      $('task-error').textContent = '';
      const library = (data.library_path || '我的音乐').split('/').filter(Boolean).pop() || '我的音乐';
      $('music-target').textContent = '入库位置：' + library + ' / 磁力下载';
      const host = $('music-tasks'); host.replaceChildren();
      if (!data.tasks?.length) { const empty = document.createElement('p'); empty.className = 'empty-tasks'; empty.textContent = '还没有音乐下载任务'; host.append(empty); return; }
      for (const task of data.tasks) {
        const card = document.createElement('article'); card.className = 'task-card';
        const top = document.createElement('div'); top.className = 'task-top';
        const name = document.createElement('span'); name.className = 'task-name'; name.textContent = task.name || task.hash;
        const state = document.createElement('span'); state.className = 'task-state';
        const progress = Math.max(0, Math.min(1, Number(task.progress) || 0));
        const downloading = task.state === 'stalledDL' ? '等待资源' : /meta/i.test(task.state || '') ? '等待种子元数据' : /stopped|paused/i.test(task.state || '') ? '已暂停' : progress >= 1 ? '等待入库' : progress > 0 ? '下载中' : '等待下载';
        const label = ['stabilizing','error','copying'].includes(task.import_state) ? labels[task.import_state] : downloading;
        state.textContent = task.import_state === 'imported' ? '已入库 · ' + (task.imported_files || 0) + ' 首' : label + ' · ' + Math.floor(progress * 100) + '%';
        top.append(name, state); card.append(top);
        const meter = document.createElement('progress'); meter.max = 1; meter.value = progress; card.append(meter);
        const detail = document.createElement('p');
        detail.textContent = task.import_state === 'imported' ? (data.scan_pending ? '文件已入库，正在等待曲库扫描。' : '回到播放器，在“我的音乐”中查看。') : task.state === 'metaDL' ? '正在等待种子元数据。长期没有进度时，可导入同一资源的 .torrent 文件。' : task.message || '完成后会自动核对并入库，通常需要 1–2 分钟。';
        card.append(detail); host.append(card);
      }
    } catch (e) { $('task-error').textContent = e.message; }
  }
  async function submit(body, torrent = false) {
    if (busy) return;
    busy = true; $('music-download-form').querySelector('button').disabled = true; $('music-torrent').disabled = true;
    say('正在提交并核对下载任务…');
    try {
      const url = new URL(endpoint); if (torrent) url.searchParams.set('api', 'downloads/torrent');
      const result = await request(url, body);
      say(result.status === 'exists' ? '音乐任务已存在，无需重复添加。' : result.status === 'metadata_pending' ? '种子文件已提交，下载器仍在等待元数据，请查看任务进度。' : result.status === 'metadata_added' ? '种子元数据已补充，正在继续下载。' : '音乐任务已添加，下载完成后会自动入库。');
      $('music-magnet').value = ''; await refresh();
    } catch (e) { say(e.message, true); }
    finally { busy = false; $('music-download-form').querySelector('button').disabled = false; $('music-torrent').disabled = false; $('music-torrent').value = ''; }
  }
  $('music-download-form').onsubmit = e => { e.preventDefault(); const magnet = $('music-magnet').value.trim(); if (!magnet.startsWith('magnet:?')) { say('请粘贴有效的磁力链接。', true); return; } submit({magnet}); };
  $('music-torrent').onchange = async e => {
    const file = e.target.files[0]; if (!file) return;
    if (file.size > 700 * 1024 || !file.name.toLowerCase().endsWith('.torrent')) { say('请选择不超过 700 KB 的 .torrent 文件。', true); e.target.value = ''; return; }
    const raw = new Uint8Array(await file.arrayBuffer()); let text = '';
    for (let start = 0; start < raw.length; start += 8192) text += String.fromCharCode(...raw.subarray(start, start + 8192));
    submit({torrent: btoa(text)}, true);
  };
  $('refresh-downloads').onclick = refresh;
  refresh(); setInterval(() => { if (!document.hidden && !busy) refresh(); }, 15000);
})();
