# Music / Songloft

The Music entry is an owner-scoped native CGI gateway and a small library/player UI for an optional Songloft server. It uses Songloft's documented login, refresh, health, song-list, cover, and playback APIs. The integration does not vendor Songloft or install a server at runtime.

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
- requires an explicitly chosen administrator username/password;
- does not use host networking, privileged mode, Docker socket access, or automatic installation.

The NAS administrator must copy the example to a private `.env`, choose host paths and a strong password, and start the service through the NAS's normal container management process. This repository cannot claim that the service is installed or running; the target NAS was not reachable during development.

The shared plugin settings should contain the loopback origin used by the gateway:

```json
{
  "music": {
    "songloft_url": "http://127.0.0.1:58091"
  },
  "ports": {
    "songloft": 58091
  }
}
```

`music.songloft_url` is the preferred explicit setting. If it is omitted, the gateway uses `ports.songloft` when present and constructs a loopback URL. A URL may include Songloft's documented `BASE_PATH`; it may not contain user info, a query, or a fragment. The host must be loopback or match the configured `nas_host`, keeping the gateway fixed to the NAS. Keep the service local to the NAS unless a separately reviewed reverse proxy is required.

## Gateway and authentication boundary

The native entry is `music/music.cgi`, registered with plugin ID `music` and `/index.html#/` as its entry. Every request first checks `settings.owner` and `settings.owner_uid`, including health/status, login, static assets, API calls, and media playback.

The gateway has fixed upstream routes. It accepts only:

- `POST /api/login` and `POST /api/refresh`, mapped to Songloft's documented JWT endpoints;
- `GET /api/status`, which probes the public Songloft health endpoint and reports `configured`/`online` honestly;
- read-only song and playlist listing, song details, cover, lyric, track metadata, and `/play` or `/play.m3u8` paths;
- `POST /api/logout`, mapped to Songloft token revocation.

There is no `url=`/host override, arbitrary path proxy, mutation endpoint, or external stream relay. The only upstream host is the configured Songloft origin. The UI uses a namespaced `X-Music-Authorization` header and an HttpOnly `songloft_access` cookie for the HTML audio element; generic Xiaomi `Authorization` headers and all incoming cookies other than these gateway-owned cookies are ignored. Credentials and tokens are never written to source files or logs.

Playback preserves upstream `Range`, `Content-Range`, `Accept-Ranges`, and `Content-Length` headers and copies the response in chunks. It does not read a complete media file into memory. A missing/invalid configuration returns a clear unconfigured state, and an unreachable service returns a clear offline state rather than simulated songs.

## Optional MIoT speaker plugin

Songloft's official plugin registry references [`songloft-org/songloft-plugin-miot`](https://github.com/songloft-org/songloft-plugin-miot). Its current metadata identifies the plugin as `miot`, version `2026.10.15.7`, minimum Songloft host `2.9.5`, and declares `net`, `websocket`, `command`, storage, and song/playlist permissions. Its README says the plugin does not include a third-party device protocol or account system; the user must provide and manage the Xiaomi account and devices, then choose managed speakers in the plugin settings.

This bundle does not install or enable the MIoT plugin. No target speaker model, Xiaomi account, or device credential was supplied, and the NAS could not be reached for validation. If the user later chooses to install it from the official release, use a Songloft address reachable by the speaker on the LAN. The plugin source explicitly warns that a loopback address such as `127.0.0.1` cannot be reached by a speaker; configure its documented LAN server address and token flow separately. Do not put Xiaomi credentials in this repository or in the Music gateway settings.

## Verification

The focused tests in [`tests/test_music.py`](../tests/test_music.py) cover native `FILE_URI` parsing, owner gating, fixed-host/path isolation, Songloft configuration states, credential-header isolation, upstream error handling, and range streaming/header preservation. They use an in-process HTTP fixture and never require a live NAS or Songloft deployment.
