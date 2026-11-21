# pley302

A 302 redirect service for an Emby-compatible media server whose library lives
in [115](https://115.com). It sits in front of the server, rewrites each
`PlaybackInfo` answer so the player asks *this* service for the media, and
answers that request with a `302` to the cloud drive's own CDN — so the bytes
never travel through your server.

Works in front of Pley and any other server that speaks the
`/Items/{id}/PlaybackInfo`, `/Videos/{id}/stream` part of the API.

```
player ──PlaybackInfo──▶ pley302 ──▶ Emby-compatible server
   ◀── MediaSources[].Path rewritten to pley302 ──┘
player ──GET /videos/{id}/stream?MediaSourceId=…──▶ pley302
   ◀── 302 Location: https://cdn….115cdn.net/… ───┘
player ──Range requests──────────────────────────▶ 115 CDN
```

## How it decides what to send

A play request carries a `MediaSourceId`; the service looks the source up,
takes its `Path`, and turns that path into a cloud URL:

| `filepath_mapping` right side | What happens |
|---|---|
| a plain path (`/Media`) | treated as a path in the 115 account, resolved to a CDN link |
| a URL (`https://openlist.example.com/d/115open`) | 302 straight to it |
| `pickcode:abcdef` | a 115 pickcode, resolved to a CDN link |

Rules are applied longest-prefix first, one `a => b` per line. `local_filepath`
prefixes are served as bytes by this service (with `Range` support), and
`direct_path` prefixes are passed to the server untouched.

## Install

```bash
git clone https://github.com/dydydd/pley302
cd pley302
uv venv --python 3.12 .venv && . .venv/bin/activate
uv pip install -r requirements.txt
cp config.example.yaml config.yaml     # fill in the cookie and the server address
python -m pley302                      # reads ./config.yaml, or $PLEY302_CONFIG
```

Or with Docker:

```bash
docker build -t pley302 .
docker run -d --name pley302 --network host \
  -v "$PWD/config.yaml:/app/config.yaml:ro" \
  -v /mnt/local:/mnt/local:ro \
  pley302
```

Point the app at the service instead of the server — the address stays the same,
only the port changes (`proxy_port`, 8098 in the example). Everything that is
not a play request is passed through, so browsing, artwork, subtitles and
playback reporting behave as before.

## Configure

```yaml
server:
  admin_port: 15896          # admin panel and cache API
  admin_token: ""            # set it if the panel is reachable from outside
  file_id_ttl: 3600          # 115 path -> file id, kept this long
  file_id_cache_file: ""     # also keep them across restarts (a file path)
  url_reuse_seconds: 60      # how long a minted CDN link is handed out again
  verify_redirect: false     # probe each link before handing it out (+0.1 s)

p115:
  cookie: ""                 # the 115 cookies: UID, CID, SEID and KID

emby_servers:
  - name: home
    host: http://127.0.0.1:8096
    proxy_port: 8098
    switch: true
    filepath_mapping: |
      /mnt/clouddrive/115/Media => /Media
    local_filepath: |
      /mnt/local
    direct_path: |
      /mnt/livetv
```

## `.strm` libraries

A `.strm` file's first line has to be an address for most servers, so a library
generated with plain paths can't play. `scripts/rewrite_strm.py` rewrites each
file into `http://<host>:<port>/d/<path>`, which this service answers with the
302. It keeps the original as `<file>.strm.bak`:

```bash
python scripts/rewrite_strm.py --root /mnt/library --base http://127.0.0.1:8098
python scripts/rewrite_strm.py --root /mnt/library --base http://127.0.0.1:8098 --apply
```

When the server runs in Docker, the address must be one the container reaches:
its network's gateway (`docker inspect` shows it as
`.NetworkSettings.Networks.<net>.Gateway`), not `127.0.0.1`. If the container sends its outbound traffic through a proxy, add
that address to its `NO_PROXY` — a proxy will answer `502` for a private address
it cannot route to.

`/r/<path>` is the same resolution without the redirect: the bytes come through
this service. Useful when a player cannot follow a `302`, at the cost of your
bandwidth.

## Admin panel

`admin_port` serves a small page (health, servers, the redirect cache that was
handed out, clear) plus the same data as JSON:

```text
GET  /                                     the page
GET  /api/v1/health
GET  /api/v1/reverse/cache/url_cache        Authorization: Bearer <admin_token>
POST /api/v1/reverse/cache/clear_url_cache  {"key": "…"}
POST /api/v1/reverse/cache/clear_all_url_cache
```

## Performance

Measured on a 1080p remux in a 115 account, `curl` against the service:

| | first request | after |
|---|---|---|
| no caches | 1.5 s | 150–270 ms |
| with the caches below | 1.0 s cold / 0.45 s after a restart | **1.4 ms** |
| play request right after `PlaybackInfo` | | **1.7 ms** |

Three things make the difference: the 115 path → file id answer is cached (and
optionally written to disk), a minted CDN link is reused for a window instead of
being minted again, and the link is minted in the background while
`PlaybackInfo` is being answered.

115 refuses extra *concurrent* opens of one file
(`{"message":"115 pmt 3-2","status":403}`), which is why a link is only shared a
couple of times in a burst (`url_share_limit`) and why the `/r` path serializes
per file.

## Tests

```bash
python -m pytest -q
```

## Notes

- 115 binds a download link to the User-Agent that asked for it, so links are
  minted with the player's own UA and cached per UA. A link is worthless to any
  other client.
- No administrator key is needed: `PlaybackInfo` is fetched with the token the
  player already sent.
- Only the pieces a 302 needs are intercepted. Transcoding is left to the
  server; `rewrite_transcoding` in `rewrite.py` can point it here too, but that
  is not the default.

## 中文速用

`pley302` 坐在 Emby/Pley 前面，把 `PlaybackInfo` 里的播放地址改写成自己，播放请求回来时直接
`302` 到 115 的 CDN，流量不经过服务器。填 `p115.cookie` 和 `emby_servers[].host`，
客户端连 `proxy_port` 即可；`.strm` 库用 `scripts/rewrite_strm.py` 换成地址形式。
缓存、管理面板与调参见上文。

## License

AGPL-3.0-only. See [LICENSE](LICENSE).
