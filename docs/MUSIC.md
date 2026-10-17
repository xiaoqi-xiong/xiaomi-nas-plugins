# Music / Songloft

The Music entry opens the official Flutter Web player supplied by the pinned Songloft server, behind an owner-scoped native CGI gateway. The earlier small library/player page remains a compatibility fallback at `index.html?embedded=1`. The integration does not vendor Songloft or install a server at runtime.

Songloft is a separate upstream project for managing music that the user legally owns. Its official README documents local library scanning, JWT access and refresh tokens, a built-in Web UI, the ARM64 Linux binary/image, and the REST endpoints used here:

- [Songloft README](https://raw.githubusercontent.com/songloft-org/songloft/main/README.md)
- [Song API reference](https://github.com/songloft-org/songloft/blob/main/docs/repowiki/API%20%E6%8E%A5%E5%8F%A3%E5%8F%82%E8%80%83/%E6%AD%8C%E6%9B%B2%20API.md)
- [Authentication API reference](https://github.com/songloft-org/songloft/blob/main/docs/repowiki/API%20%E6%8E%A5%E5%8F%A3%E5%8F%82%E8%80%83/%E8%AE%A4%E8%AF%81%20API.md)
- [Release v2.13.3](https://github.com/songloft-org/songloft/releases/tag/v2.13.3)
- [Pinned Docker Hub manifest metadata](https://hub.docker.com/v2/repositories/songloft/songloft/tags/2.13.3)

## Configure the local service

The repository contains a reviewable Compose template at [`deployment/songloft.compose.yml`](../deployment/songloft.compose.yml) and a placeholder environment file at [`deployment/songloft.env.example`](../deployment/songloft.env.example). The template:

- pins the official `2.13.3` image and ARM64 platform;
- binds port `58091` to `127.0.0.1` only;
- mounts the music library read-only at `/app/music`;
- keeps Songloft's database and generated data in a separate writable `/app/data` mount;
- runs the pinned binary directly as the configured UID/GID (the upstream PUID entrypoint tries to chown the read-only music mount and exits);
- requires an explicitly chosen administrator username/password;
- does not use host networking, privileged mode, Docker socket access, or automatic installation.

For a new installation, copy the example to a private `.env`, choose host paths and a strong password, and start the service through the NAS's normal container management process. A template alone does not prove installation; check the health, authenticated listing, scan result, Range response, and native player against the target service.

The shared plugin settings should contain the loopback origin used by the gateway:

```json
{
  "music": {
    "songloft_url": "http://127.0.0.1:58091",
    "qb_host": "127.0.0.1"
  },
  "ports": {
    "songloft": 58091
  }
}
```

`music.songloft_url` is the preferred explicit setting. If it is omitted, the gateway uses `ports.songloft` when present and constructs a loopback URL. A URL may include Songloft's documented `BASE_PATH`; it may not contain user info, a query, or a fragment. The host must be loopback or match the configured `nas_host`, keeping the gateway fixed to the NAS. Keep the service local to the NAS unless a separately reviewed reverse proxy is required.

`music.qb_host` selects the host for the optional public qBittorrent music importer and defaults to `nas_host` for existing installations. It is a host name or IP address only, without a scheme, credentials, or path. This setting can point at a separate downloader host while Songloft and the native gateway remain on `nas_host`.

## Gateway and authentication boundary

The native entry is `music/music.cgi`, registered with plugin ID `music` and `/index.html#/` as its entry. Every request first checks `settings.owner` and `settings.owner_uid`, including health/status, login, static assets, API calls, and media playback.

The compatibility gateway has fixed upstream routes:

- `POST /api/login` and `POST /api/refresh`, mapped to Songloft's documented JWT endpoints;
- `GET /api/status`, which probes the public Songloft health endpoint and reports `configured`/`online` honestly;
- read-only song and playlist listing, song details, cover, lyric, track metadata, and `/play` or `/play.m3u8` paths;
- `POST /api/logout`, mapped to Songloft token revocation.
- `GET/POST/DELETE /api/session`, for owner-scoped remembered login and forgetting the saved Music account;
- `POST /api/scan` and `GET /api/scan/progress`, for a fixed non-reimporting local-library scan and its progress.

The official player lives under `vendor-ui/`; its static files and normal `/api/v1/` operations are forwarded to the same fixed Songloft origin after the native owner gate. It can manage Songloft's own library metadata, playlists, settings, and scans as the application's administrator. The separate compatibility API remains narrow. Neither path accepts an upstream-host override, and incoming Xiaomi credentials/cookies are never forwarded. Original source audio is protected by the container's mount permissions.

`songloft-ui-bridge.js` restores the pinned Flutter SharedPreferences token contract before Flutter starts, separates application Bearer headers into `X-Music-Authorization`, and removes application tokens from generated media URLs. CanvasKit and font fallbacks load from the local service. The native shell hosts this page in a real iframe. Native audio requests use the NAS-side cached application session when browser media cookies are unavailable.

Remembered Music credentials live under the `music` key of `private/native-login.json` (0600); `private/music-session.json` (0600) caches the access/refresh token pair, renews near expiry, and retries one invalid session. Login and cache updates share the native-login file lock with other applications. The frontend restores a session on opening, and explicit logout removes only Music's saved account/cache. Credentials and tokens never enter public source files or logs.

The native page exposes **扫描曲库** and reports real scan completion. Configure the server's automatic scan interval to 300 seconds when using the “我的音乐” folder workflow; keep automatic fingerprint calculation disabled on small NAS devices unless needed. Mount original audio read-only, and keep the writable database separate. A read-only extra audio source can be included for existing tracks without moving or modifying downloader files.

The Music window also keeps **播放器 / 搜音乐 / 下载任务** in separate iframes. Search opens the existing PanSou service. Its magnet results offer **音乐自动入库**; users can also paste a magnet or upload a `.torrent` file from **下载任务**. Cloud-drive share links still require their respective cloud downloader and account.

Music downloads use the existing public qBittorrent service only. Configure `music.download_root` (host path), `music.download_qb_path` (the matching container path), and `music.library_path` (the formal library). New tasks receive the `native-music` tag and a private per-hash admission record. The gateway never changes global downloader preferences, existing tasks, PT settings, or container mounts. A hash already present outside this queue is not adopted or moved.

Run `music/import_music.py` periodically as the configured owner user. It only imports admitted, tagged, completed audio files beneath the configured music download root. It waits for matching file size/mtime observations, copies into a staging directory, publishes the album atomically under `我的音乐/磁力下载`, and retries a pending Songloft scan. Add `.native-music-staging` to Songloft's `exclude_dirs` before enabling the timer: the upstream scanner does not automatically ignore hidden directories, so temporary copies must be excluded explicitly. Original files remain seedable. Existing destination folders without a matching importer marker are preserved. Each task is limited to 128 audio files and 2 GiB per import; ZIP archives are not extracted. A magnet can wait indefinitely if no peer supplies its metadata; the `.torrent` option supplies metadata, while peers or the torrent's web seeds still have to deliver the audio.

Playback preserves upstream `Range`, `Content-Range`, `Accept-Ranges`, and `Content-Length` headers and copies the response in chunks. It does not read a complete media file into memory. A missing/invalid configuration returns a clear unconfigured state, and an unreachable service returns a clear offline state rather than simulated songs.

## Optional MIoT speaker plugin

Songloft's official plugin registry references [`songloft-org/songloft-plugin-miot`](https://github.com/songloft-org/songloft-plugin-miot). Its current metadata identifies the plugin as `miot`, version `2026.10.15.7`, minimum Songloft host `2.9.5`, and declares `net`, `websocket`, `command`, storage, and song/playlist permissions. Its README says the plugin does not include a third-party device protocol or account system; the user must provide and manage the Xiaomi account and devices, then choose managed speakers in the plugin settings.

This bundle does not install or enable the MIoT plugin. No target speaker model, Xiaomi account, or device credential was supplied, and the NAS could not be reached for validation. If the user later chooses to install it from the official release, use a Songloft address reachable by the speaker on the LAN. The plugin source explicitly warns that a loopback address such as `127.0.0.1` cannot be reached by a speaker; configure its documented LAN server address and token flow separately. Do not put Xiaomi credentials in this repository or in the Music gateway settings.

## Verification

The focused tests in [`tests/test_music.py`](../tests/test_music.py) cover native `FILE_URI` parsing, owner gating, fixed-host/path isolation, Songloft configuration states, credential-header isolation, upstream error handling, and range streaming/header preservation. They use an in-process HTTP fixture and never require a live NAS or Songloft deployment.
