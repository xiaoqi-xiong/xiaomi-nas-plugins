(function () {
  "use strict";

  const tokenKey = "xiaomi-music-songloft-tokens";
  const notice = document.getElementById("notice");
  const login = document.getElementById("login");
  const library = document.getElementById("library");
  const logout = document.getElementById("logout");
  const player = document.getElementById("player");
  const audio = document.getElementById("audio");
  const songs = document.getElementById("songs");
  const more = document.getElementById("more");
  const baseUrl = (function () {
    try {
      const hinted = window.__MICRO_APP_PUBLIC_PATH__;
      const candidate = new URL(hinted || ".", document.baseURI);
      if (candidate.origin === window.location.origin && candidate.pathname.indexOf("/music") >= 0) return candidate;
    } catch (_) {}
    return new URL(".", window.location.href);
  }());
  const route = function (path) {
    if (path.indexOf("api/") === 0) {
      const separator = path.indexOf("?");
      const apiPath = separator < 0 ? path.slice(4) : path.slice(4, separator);
      const url = new URL("music.cgi", baseUrl);
      url.searchParams.set("api", apiPath);
      if (separator >= 0) {
        new URLSearchParams(path.slice(separator + 1)).forEach(function (value, key) { url.searchParams.append(key, value); });
      }
      return url.href;
    }
    return new URL(path, baseUrl).href;
  };
  let offset = 0;
  let keyword = "";
  let hasMore = false;
  let playbackGeneration = 0;

  function tokens() {
    try { return JSON.parse(localStorage.getItem(tokenKey) || "null"); } catch (_) { return null; }
  }

  function saveTokens(value) {
    if (value && value.access_token && value.refresh_token) localStorage.setItem(tokenKey, JSON.stringify(value));
    else localStorage.removeItem(tokenKey);
  }

  function setNotice(message, state) {
    notice.textContent = message;
    notice.className = "notice" + (state ? " " + state : "");
  }

  function stopPlayback() {
    playbackGeneration += 1;
    audio.pause();
    audio.removeAttribute("src");
    audio.replaceChildren();
    audio.load();
    player.hidden = true;
  }

  function playbackError() {
    stopPlayback();
    document.getElementById("library-error").textContent = "播放失败：无法读取音频，请检查登录状态或歌曲文件。";
  }

  function showLogin(message) {
    stopPlayback();
    login.hidden = false;
    library.hidden = true;
    logout.hidden = true;
    if (message) document.getElementById("login-error").textContent = message;
  }

  async function request(path, options, retry) {
    const opts = Object.assign({ headers: {} }, options || {});
    const current = tokens();
    if (current && current.access_token) opts.headers["X-Music-Authorization"] = "Bearer " + current.access_token;
    const response = await fetch(route(path), opts);
    if (response.status === 401 && !retry && current && current.refresh_token) {
      const refreshed = await fetch(route("api/refresh"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: current.refresh_token }) });
      if (refreshed.ok) { saveTokens(await refreshed.json()); return request(path, options, true); }
      saveTokens(null);
    }
    return response;
  }

  async function status() {
    try {
      const response = await fetch(route("api/status"), { cache: "no-store" });
      if (!response.ok) {
        setNotice("无法读取 Music 服务状态。", "warn");
        showLogin("网关状态检查失败");
        return false;
      }
      const data = await response.json();
      if (!data.configured) {
        setNotice("Songloft 尚未配置。请先在本机设置中配置服务地址，并按 MUSIC.md 部署服务。", "warn");
        showLogin("服务未配置");
        return false;
      }
      if (!data.online) {
        setNotice("Songloft 服务当前不可达。请确认容器已启动并监听 NAS 本机地址。", "warn");
        showLogin("服务不可达");
        return false;
      }
      setNotice("Songloft 已连接", "ok");
      return true;
    } catch (_) {
      setNotice("无法读取 Music 服务状态。", "warn");
      showLogin("网关状态检查失败");
      return false;
    }
  }

  function coverUrl(song) {
    if (!song.cover_url) return "";
    return song.cover_url.indexOf("/api/") === 0 ? route(song.cover_url.slice(1)) : "";
  }

  function draw(list) {
    list.forEach(function (song) {
      const card = document.createElement("article");
      card.className = "song";
      const cover = document.createElement("img");
      cover.className = "cover";
      cover.alt = "";
      cover.src = coverUrl(song) || route("music.cgi?asset=icon.svg");
      const info = document.createElement("div");
      info.className = "song-info";
      const title = document.createElement("span");
      title.className = "song-title";
      title.textContent = song.title || "未命名歌曲";
      const artist = document.createElement("span");
      artist.className = "song-artist";
      artist.textContent = [song.artist, song.album].filter(Boolean).join(" · ") || "未知艺术家";
      info.append(title, artist);
      const button = document.createElement("button");
      button.className = "play";
      button.type = "button";
      button.setAttribute("aria-label", "播放 " + title.textContent);
      button.textContent = "▶";
      button.addEventListener("click", function () { play(song); });
      card.append(cover, info, button);
      songs.append(card);
    });
  }

  async function loadSongs(reset) {
    const error = document.getElementById("library-error");
    if (reset) { offset = 0; songs.replaceChildren(); }
    error.textContent = "";
    const params = new URLSearchParams({ limit: "40", offset: String(offset) });
    if (keyword) params.set("keyword", keyword);
    try {
      const response = await request("api/songs?" + params.toString());
      if (response.status === 401) { showLogin("登录已过期，请重新登录"); return; }
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || "读取曲库失败");
      const list = Array.isArray(data.songs) ? data.songs : [];
      draw(list);
      offset += list.length;
      hasMore = offset < Number(data.total || 0);
      more.hidden = !hasMore;
      document.getElementById('library-count').textContent = '共 ' + Number(data.total || 0) + ' 首';
      if (!list.length && !offset) error.textContent = "曲库为空。把歌曲放入小米“我的音乐”目录后，点击“扫描曲库”。";
    } catch (err) { error.textContent = err.message || "读取曲库失败"; }
  }

  function play(song) {
    const error = document.getElementById("library-error");
    const source = document.createElement("source");
    source.src = route("api/v1/songs/" + encodeURIComponent(String(song.id)) + "/play");
    stopPlayback();
    const generation = playbackGeneration;
    audio.replaceChildren(source);
    audio.load();
    error.textContent = "";
    const showPlayback = function () {
      if (generation !== playbackGeneration) return;
      document.getElementById("now-title").textContent = song.title || "未命名歌曲";
      document.getElementById("now-artist").textContent = song.artist || "";
      player.hidden = false;
    };
    const showPlaybackError = function () {
      if (generation === playbackGeneration) playbackError();
    };
    try {
      const playback = audio.play();
      if (playback && typeof playback.then === "function") playback.then(showPlayback).catch(showPlaybackError);
      else showPlayback();
    } catch (_) { showPlaybackError(); }
  }

  audio.addEventListener("error", playbackError);

  document.getElementById("login-form").addEventListener("submit", async function (event) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const error = document.getElementById("login-error");
    error.textContent = "";
    try {
      const response = await fetch(route("api/session"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: form.get("username"), password: form.get("password") }) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || "登录失败");
      saveTokens(data);
      formElement.reset();
      await showLibrary();
    } catch (err) { error.textContent = err.message || "登录失败"; }
  });
  document.getElementById("search-form").addEventListener("submit", function (event) { event.preventDefault(); keyword = document.getElementById("search").value.trim(); loadSongs(true); });
  more.addEventListener("click", function () { if (hasMore) loadSongs(false); });
  logout.addEventListener("click", async function () {
    stopPlayback();
    const forgotten = await fetch(route('api/session'), {method: 'DELETE', cache: 'no-store'}).catch(() => null);
    if (!forgotten?.ok) { setNotice('未能关闭自动登录，请重试', 'warn'); return; }
    try { await request("api/logout", { method: "POST" }); } catch (_) {}
    saveTokens(null); showLogin("已退出 Songloft");
  });

  document.getElementById('scan').addEventListener('click', async function () {
    const button = this;
    button.disabled = true;
    try {
      const response = await request('api/scan', {method: 'POST'});
      if (!response.ok && response.status !== 409) throw Error('扫描未能开始，请重试');
      setNotice('正在扫描曲库…', 'ok');
      const timer = setInterval(async function () {
        try {
          const progressResponse = await request('api/scan/progress');
          if (!progressResponse.ok) throw Error();
          const progress = await progressResponse.json();
          if (['idle', 'completed', 'failed', 'cancelled'].includes(progress.status)) {
            clearInterval(timer); button.disabled = false;
            setNotice(progress.status === 'failed' ? '扫描失败，请检查音乐目录' : '曲库扫描完成', progress.status === 'failed' ? 'warn' : 'ok');
            await loadSongs(true);
          }
        } catch (_) { clearInterval(timer); button.disabled = false; setNotice('无法读取扫描状态，请重试', 'warn'); }
      }, 1500);
    } catch (err) { button.disabled = false; setNotice(err.message, 'warn'); }
  });

  async function showLibrary() {
    login.hidden = true;
    library.hidden = false;
    logout.hidden = false;
    await loadSongs(true);
  }

  const back = document.getElementById("native-back");
  const mobileHost = !!(window.flutter_inappwebview || window.android_webview) || /Android|iPhone|iPad/i.test(navigator.userAgent) || (navigator.maxTouchPoints > 1 && /Macintosh/i.test(navigator.userAgent));
  if (mobileHost && document.body.hasAttribute('data-xiaomi-shell')) {
    back.hidden = false;
    back.addEventListener("click", function () {
      if (typeof window.hs_callHandler === "function") window.hs_callHandler("normal_goback", {}, function () {});
    });
  }

  (async function init() {
    if (!(await status())) return;
    const remembered = await fetch(route('api/session'), {cache: 'no-store'}).catch(() => null);
    if (remembered?.ok) saveTokens(await remembered.json());
    if (tokens()) await showLibrary();
    else showLogin();
  }());
}());
