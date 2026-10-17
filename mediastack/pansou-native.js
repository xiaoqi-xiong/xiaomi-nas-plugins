// Explicit actions only: preserve the existing downloader flow and add a
// bounded music queue that imports finished audio without moving seeding files.
(() => {
  const base = new URL('.', location.href);
  const regularEndpoint = new URL('../native/download', base);
  const musicEndpoint = new URL('../native/music', base);
  const musicMode = new URL(location.href).searchParams.get('music') === '1';
  const dialog = document.createElement('dialog'); dialog.className = 'native-download-dialog';
  dialog.innerHTML = '<header><h2>下载到…</h2><button type="button" class="native-close" aria-label="关闭下载选择">✕</button></header><form><p class="native-resource-name"></p><label for="native-magnet">磁力链接</label><textarea id="native-magnet" required spellcheck="false" placeholder="magnet:?xt=urn:btih:…"></textarea><label for="native-target">保存方式</label><select id="native-target"><option value="normal">常规下载</option><option value="music">音乐自动入库</option></select><label for="native-downloader">下载器</label><select id="native-downloader"><option value="public">公共下载器（代理）</option><option value="pt">PT 下载器（直连，仅私有资源）</option></select><p class="native-save-path"></p><p class="native-warning" hidden>盘搜的公开磁力资源建议使用公共下载器。PT 下载器仅用于私有站点资源。</p><button class="native-submit" type="submit">确认下载</button><p class="native-result" role="status"></p></form>';
  let busy = false, optionRequest = 0;
  const $ = selector => dialog.querySelector(selector);
  function isMusic() { return $('#native-target').value === 'music'; }
  async function options() {
    const request = ++optionRequest, music = isMusic(), select = $('#native-downloader');
    if (music) select.value = 'public';
    select.disabled = music || busy;
    const chosen = select.value, button = $('.native-submit'), result = $('.native-result'), path = $('.native-save-path');
    button.disabled = true; path.textContent = '正在读取保存位置…'; result.textContent = ''; result.dataset.error = 'false';
    $('.native-warning').hidden = music || chosen !== 'pt';
    try {
      const response = await fetch(music ? musicEndpoint : regularEndpoint + '?downloader=' + chosen, {cache:'no-store'});
      const data = await response.json(); if (request !== optionRequest) return;
      if (!response.ok) throw Error(data.message || data.error || '读取下载器失败');
      path.textContent = music ? '下载完成后自动加入“我的音乐”。原文件保留做种。' : '保存位置：' + data.save_path;
      button.disabled = false;
    } catch (e) { if (request !== optionRequest) return; path.textContent = ''; result.textContent = e.message; result.dataset.error = 'true'; }
  }
  function open(url, title, target = musicMode ? 'music' : 'normal') {
    if (!dialog.isConnected) {
      document.body.append(dialog);
      $('.native-close').onclick = () => { if (!busy) dialog.close(); };
      dialog.oncancel = e => { if (busy) e.preventDefault(); };
      $('#native-downloader').onchange = options; $('#native-target').onchange = options;
      $('form').onsubmit = submit;
    }
    $('.native-resource-name').textContent = title;
    $('#native-magnet').value = url; $('#native-target').value = target; $('#native-downloader').value = 'public';
    $('.native-result').textContent = ''; dialog.showModal(); options();
  }
  async function submit(e) {
    e.preventDefault(); if (busy) return;
    const app = $('#native-downloader').value, music = isMusic(), magnet = $('#native-magnet').value.trim();
    if (!magnet.startsWith('magnet:?')) { $('.native-result').textContent = '请粘贴有效的磁力链接。'; return; }
    if (!music && app === 'pt' && !confirm('这是直连的 PT 下载器。确认此链接来自允许使用的私有站点？公开磁力建议选公共下载器。')) return;
    busy = true; $('#native-downloader').disabled = true; $('#native-target').disabled = true; $('#native-magnet').disabled = true;
    const button = $('.native-submit'), result = $('.native-result'); button.disabled = true; result.textContent = '正在提交并检查任务…'; result.dataset.error = 'false';
    try {
      const response = await fetch(music ? musicEndpoint : regularEndpoint, {method:'POST',cache:'no-store',headers:{'Content-Type':'application/json'},body:JSON.stringify(music ? {magnet} : {downloader:app,magnet})});
      const data = await response.json(); if (!response.ok) throw Error(data.message || data.error || '提交失败');
      result.textContent = data.status === 'exists' ? '此任务已存在，无需重复添加。' : '任务已添加到下载器。';
      result.textContent += music ? '\n完整音频下完后自动加入“我的音乐”，可在音乐 → 下载任务查看进度。' : '\n保存位置：' + data.save_path;
      if (data.name) result.textContent += '\n' + data.name;
    } catch (e) { result.textContent = e.message; result.dataset.error = 'true'; }
    finally { busy = false; button.disabled = false; $('#native-target').disabled = false; $('#native-magnet').disabled = false; $('#native-downloader').disabled = music; }
  }
  function attach() {
    if (!document.getElementById('native-add-music') && document.querySelector('.nav-header .container')) {
      const button = document.createElement('button'); button.id = 'native-add-music'; button.type = 'button'; button.className = 'native-download-button'; button.textContent = '添加音乐磁力';
      button.onclick = () => open('', '添加音乐磁力链接', 'music'); document.querySelector('.nav-header .container').append(button);
    }
    for (const element of document.querySelectorAll('.result-item .result-link')) {
      const row = element.closest('.result-item'), url = element.textContent.trim(); let button = row.querySelector('.native-download-button');
      if (!url.startsWith('magnet:?')) { if (button) button.remove(); continue; }
      if (!button) { button = document.createElement('button'); button.type = 'button'; button.className = 'native-download-button'; button.textContent = musicMode ? '下载到我的音乐' : '下载到…'; row.append(button); }
      button.onclick = e => { e.stopPropagation(); open(url, row.querySelector('.result-title-button')?.textContent.trim() || '磁力资源'); };
    }
  }
  const observer = new MutationObserver(attach); observer.observe(document.documentElement, {subtree:true,childList:true}); attach();
})();
