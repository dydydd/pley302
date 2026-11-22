"""The proxy itself: one ASGI app per configured Emby server.

Requests fall into four buckets, in this order:

1. ``Items/{id}/PlaybackInfo`` - answered by the real server, then rewritten so
   every MediaSource points back at this proxy.
2. ``Videos|Audio/{id}/stream`` / ``original`` / ``Items/{id}/Download`` - the
   play request: mapped to a 115 path or an OpenList URL and answered with 302.
3. Emby's web player bootstrap (``basehtmlplayer.js``) - patched so a remote
   direct-play source is not fetched with ``crossorigin="anonymous"``, which a
   CDN without CORS headers would refuse.
4. Everything else - passed through unchanged, bodies streamed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Mapping
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse

from .cache import RedirectCache, TTLCache
from .config import AppConfig, EmbyServerConfig
from .emby import (
    EmbyError,
    EmbyUpstream,
    api_key_from,
    device_id_from,
    media_source_path,
    pick_media_source,
)
from .index import StrmIndex
from .mapping import PathMapper, ResolvedPath
from .p115 import P115Error, P115Resolver, P115Unusable
from .rewrite import rewrite_playback_info

log = logging.getLogger("pley302.proxy")

STREAM_RE = re.compile(
    r"^/(?:emby/)?(?P<kind>videos|audio)/(?P<item>[^/]+)/"
    r"(?P<tail>stream(?:\.[A-Za-z0-9]+)?|original(?:\.[A-Za-z0-9]+)?)$",
    re.IGNORECASE,
)
DOWNLOAD_RE = re.compile(r"^/(?:emby/)?items/(?P<item>[^/]+)/download$", re.IGNORECASE)
PLAYBACKINFO_RE = re.compile(
    r"^/(?:emby/)?items/(?P<item>[^/]+)/playbackinfo/?$", re.IGNORECASE
)
# `/d/<percent-encoded path>`: what a `.strm` file can name when the server in
# front of us refuses to take a path, and the address has to be a URL.
DIRECT_RE = re.compile(r"^/d/(?P<path>.+)$", re.IGNORECASE)
# `/r/<percent-encoded path>`: the same resolution, but the bytes come through
# this server instead of a 302. For a server in front that will not take a
# redirect (a server that probes the address first fails when it does).
RELAY_RE = re.compile(r"^/r/(?P<path>.+)$", re.IGNORECASE)
BASEHTMLPLAYER_RE = re.compile(
    r"/web/modules/htmlvideoplayer/basehtmlplayer\.js$", re.IGNORECASE
)

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}

CORS_JS_PATTERN = re.compile(
    r"""mediaSource\.IsRemote\s*&&\s*["']DirectPlay["']\s*===\s*playMethod\s*\?\s*null\s*:\s*["']anonymous["']""",
    re.IGNORECASE,
)

def ascii_location(url: str) -> str:
    """Header values are latin-1; a path with Chinese characters must be
    percent-encoded before it goes into ``Location``."""
    try:
        url.encode("latin-1")
        return url
    except UnicodeEncodeError:
        return quote(url, safe=":/?#[]@!$&'()*+,;=%")


REDIRECT_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
    "Cache-Control": "no-store",
    "Accept-Ranges": "bytes",
}


@dataclass(slots=True)
class ProxyState:
    app_config: AppConfig
    server: EmbyServerConfig
    mapper: PathMapper
    resolver: P115Resolver
    redirects: RedirectCache
    playback: TTLCache[dict[str, Any]]
    upstream: EmbyUpstream
    client: httpx.AsyncClient
    index: StrmIndex


def _filtered_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP}


def _upstream_headers(request: Request) -> dict[str, str]:
    headers = _filtered_headers(request.headers)
    headers.pop("host", None)
    headers.pop("Host", None)
    headers.pop("content-length", None)
    return headers


def _base_url(state: ProxyState, request: Request, query: Mapping[str, str]) -> str:
    if state.server.public_address:
        return state.server.public_address.rstrip("/")
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    scheme = forwarded_proto or request.url.scheme or "http"
    host = forwarded_host or request.url.netloc
    return f"{scheme}://{host}"


def _playback_query(query: Mapping[str, str]) -> dict[str, str]:
    keep = {"UserId", "userId", "api_key", "DeviceId", "deviceId", "PlaySessionId"}
    return {k: v for k, v in query.items() if k in keep}


def _content_type_for(path: str) -> str:
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


class FileRange:
    """Minimal single-range parser: ``bytes=a-b`` / ``bytes=a-`` / ``bytes=-n``."""

    def __init__(self, header: str, size: int) -> None:
        self.start = 0
        self.end = size - 1
        self.partial = False
        header = (header or "").strip()
        if not header.lower().startswith("bytes=") or size <= 0:
            return
        spec = header[6:].split(",", 1)[0].strip()
        if "-" not in spec:
            return
        first, last = spec.split("-", 1)
        first, last = first.strip(), last.strip()
        if first == "":
            suffix = int(last or 0)
            if suffix <= 0:
                return
            self.start = max(0, size - suffix)
            self.end = size - 1
            self.partial = True
            return
        start = int(first)
        end = int(last) if last else size - 1
        if start >= size:
            raise ValueError("range not satisfiable")
        self.start = start
        self.end = min(end, size - 1)
        self.partial = True

    @property
    def length(self) -> int:
        return max(0, self.end - self.start + 1)


async def _file_chunks(path: str, start: int, length: int, chunk: int = 1 << 20) -> AsyncIterator[bytes]:
    remaining = length
    with open(path, "rb") as handle:
        handle.seek(start)
        while remaining > 0:
            data = handle.read(min(chunk, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data


def serve_local_file(request: Request, path: str) -> Response:
    if not os.path.isfile(path):
        return Response(content=b"file not found", status_code=404)
    size = os.path.getsize(path)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": _content_type_for(path),
    }
    range_header = request.headers.get("range")
    parsed = FileRange(range_header or "", size)
    if parsed.partial:
        headers["Content-Range"] = f"bytes {parsed.start}-{parsed.end}/{size}"
        headers["Content-Length"] = str(parsed.length)
        if request.method.upper() == "HEAD":
            return Response(status_code=206, headers=headers)
        return StreamingResponse(
            _file_chunks(path, parsed.start, parsed.length), status_code=206, headers=headers
        )
    headers["Content-Length"] = str(size)
    if request.method.upper() == "HEAD":
        return Response(status_code=200, headers=headers)
    return StreamingResponse(_file_chunks(path, 0, size), status_code=200, headers=headers)


async def _fetch_playback_info(state: ProxyState, request: Request, item_id: str) -> dict[str, Any]:
    query = dict(request.query_params)
    api_key = api_key_from(query, request.headers)
    device_id = device_id_from(query, request.headers)
    key = (item_id, api_key)
    cached = state.playback.get(key)
    if cached is not None:
        return cached

    params = _playback_query(query) or {"api_key": api_key}
    headers = _upstream_headers(request)
    info: dict[str, Any] | None = None
    body: Any = None
    raw = await request.body()
    if raw:
        try:
            import json

            body = json.loads(raw)
        except Exception:
            body = None
    try:
        if body is not None:
            info = await state.upstream.playback_info(
                item_id, params=params, headers=headers, body=body, method="POST"
            )
        else:
            info = await state.upstream.playback_info(
                item_id, params=params, headers=headers, method="GET"
            )
    except EmbyError as exc:
        log.warning("PlaybackInfo for %s failed: %s", item_id, exc)
        if body is not None:
            info = await state.upstream.playback_info(
                item_id, params=params, headers=headers, method="GET"
            )
        else:
            raise
    if info is not None:
        state.playback.set(key, info)
    assert info is not None
    return info


def _private_host(host: str) -> bool:
    """A host only this network (or this computer) can reach."""
    import ipaddress

    if host in ("localhost", ""):
        return host == "localhost"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local


async def resolve_media_path(state: ProxyState, path: str) -> ResolvedPath:
    """A `.strm` item, or a bare file name, resolved through the library table.

    Emby reports a full path and nothing here changes; a server that reports
    only the file name of a strm item is served from the library table instead.
    """
    media = path
    if path.lower().endswith(".strm") or "/" not in path.strip("/"):
        inner = await asyncio.to_thread(state.index.target, path)
        if inner:
            media = inner
    if media.lower().startswith(("http://", "https://")):
        # Kodi's `|User-Agent=…` after an address sets headers for the server
        # that asks it; a 302 hands the address itself to the player.
        address, _, options = media.partition("|")
        if options and "=" in options:
            media = address.strip()
        # Our own /d/ address (what a rewritten .strm holds, usually on a
        # private one only this network can reach) is resolved here instead of
        # being handed to a player that cannot get to it.
        from urllib.parse import unquote, urlparse

        parsed = urlparse(media)
        inner = parsed.path
        if inner.startswith(("/d/", "/r/")) and _private_host(parsed.hostname or ""):
            prefix = 3  # len("/d/") or len("/r/")
            return state.mapper.map(unquote(inner[prefix:]))
        return ResolvedPath(kind="http", value=media, source=path)
    return state.mapper.map(media)


async def relay_media(state: ProxyState, request: Request, url: str) -> Response:
    """Send the media's bytes ourselves, for a URL that will not open for the
    player (115's edge answers 502 for some URLs it mints)."""
    headers = {}
    user_agent = request.headers.get("user-agent")
    if user_agent:
        headers["User-Agent"] = user_agent
    byte_range = request.headers.get("range")
    if byte_range:
        headers["Range"] = byte_range
    upstream = state.client.build_request(request.method, url, headers=headers)
    response = await state.client.send(upstream, stream=True)
    out_headers = _filtered_headers(response.headers)

    async def chunks() -> AsyncIterator[bytes]:
        try:
            async for chunk in response.aiter_raw():
                yield chunk
        finally:
            await response.aclose()

    log.info("relayed %s %s (%s)", request.method, url.split("/")[2], response.status_code)
    return StreamingResponse(chunks(), status_code=response.status_code, headers=out_headers)


async def _resolve_target(
    state: ProxyState,
    request: Request,
    resolved: ResolvedPath,
    original_url: str,
    device_id: str,
) -> Response:
    user_agent = request.headers.get("user-agent", "")
    if resolved.kind == "local":
        return serve_local_file(request, resolved.value)
    if resolved.kind == "http":
        redirect = resolved.value
    else:
        # Reuse a URL minted moments ago (the play request right after
        # PlaybackInfo, a player asking twice), but only a couple of times:
        # 115 refuses extra concurrent opens of the same file.
        redirect = await state.resolver.resolve(resolved, user_agent)
    state.redirects.put(original_url, user_agent, device_id, redirect)
    log.info(
        "302 %s ua=%r device=%r -> %s",
        original_url,
        user_agent[:40],
        device_id[:12],
        redirect.split("/")[2] if "//" in redirect else redirect[:40],
    )
    headers = dict(REDIRECT_HEADERS)
    headers["Location"] = ascii_location(redirect)
    return Response(status_code=302, headers=headers)


async def handle_direct(state: ProxyState, request: Request, raw_path: str) -> Response:
    """Resolve a path named in the URL itself, for `.strm` files that must hold
    an address rather than the media's path."""
    from urllib.parse import unquote

    path = unquote(raw_path)
    if not path.startswith("/"):
        path = "/" + path
    if state.mapper.is_ignored(path):
        return Response(content=b"ignored", status_code=404)
    device_id = device_id_from(dict(request.query_params), request.headers)
    resolved = state.mapper.map(path)
    try:
        return await _resolve_target(state, request, resolved, request.url.path, device_id)
    except (P115Error, ValueError) as exc:
        log.error("302 for %s failed: %s", path, exc)
        return Response(content=f"302 failed: {exc}".encode(), status_code=502)


async def handle_relay(state: ProxyState, request: Request, raw_path: str) -> Response:
    """Resolve a path named in the URL and send its bytes (no redirect)."""
    from urllib.parse import unquote

    path = unquote(raw_path)
    if not path.startswith("/"):
        path = "/" + path
    if state.mapper.is_ignored(path):
        return Response(content=b"ignored", status_code=404)
    user_agent = request.headers.get("user-agent", "")
    device_id = device_id_from(dict(request.query_params), request.headers)
    original_url = f"{request.url.path}?{request.url.query}" if request.url.query else request.url.path
    resolved = state.mapper.map(path)
    leased = ""
    try:
        async with state.resolver.slot(resolved.value):
            url = await state.resolver.lease(resolved, user_agent)
            leased = url
            state.redirects.put(original_url, user_agent, device_id, url)
            return await relay_media(state, request, url)
    except P115Unusable as exc:
        url = exc.url
    except (P115Error, ValueError) as exc:
        log.error("relay for %s failed: %s", path, exc)
        return Response(content=f"relay failed: {exc}".encode(), status_code=502)
    except httpx.HTTPError as exc:
        log.error("relay for %s failed: %s", path, exc)
        return Response(content=b"upstream would not open", status_code=502)
    finally:
        if leased:
            await state.resolver.release(leased)


async def handle_play(state: ProxyState, request: Request, item_id: str) -> Response:
    query = dict(request.query_params)
    media_source_id = (
        query.get("MediaSourceId")
        or query.get("mediaSourceId")
        or query.get("MediaSourceID")
        or ""
    )
    device_id = device_id_from(query, request.headers)
    original_url = f"{request.url.path}?{request.url.query}" if request.url.query else request.url.path

    try:
        info = await _fetch_playback_info(state, request, item_id)
    except EmbyError as exc:
        log.error("play of %s failed before the 302: %s", item_id, exc)
        return Response(content=f"playback info failed: {exc}".encode(), status_code=exc.status)
    source = pick_media_source(info, media_source_id)
    if source is None:
        log.warning("no MediaSource %r for item %s", media_source_id, item_id)
        return Response(content=b"media source not found", status_code=404)
    path = media_source_path(source)
    if not path:
        return Response(content=b"media source has no path", status_code=404)
    if state.mapper.is_ignored(path):
        return await passthrough(state, request)

    resolved = await resolve_media_path(state, path)
    try:
        return await _resolve_target(state, request, resolved, original_url, device_id)
    except P115Unusable as exc:
        log.warning("115 URL unusable for %s (%s); relaying instead", path, exc)
        if not state.app_config.server.fallback_proxy:
            return Response(content=f"302 failed: {exc}".encode(), status_code=502)
        try:
            return await relay_media(state, request, exc.url)
        except httpx.HTTPError as relay_error:
            log.error("relay for %s failed: %s", path, relay_error)
            return Response(content=b"upstream would not open", status_code=502)
    except (P115Error, ValueError) as exc:
        log.error("302 for %s failed: %s", path, exc)
        return Response(content=f"302 failed: {exc}".encode(), status_code=502)


_PREFETCHING: set[tuple[str, str]] = set()


async def prefetch_source(state: ProxyState, path: str, user_agent: str) -> None:
    """Mint the 115 URL while the client is still reading PlaybackInfo."""
    key = (path, user_agent)
    if key in _PREFETCHING:
        return
    _PREFETCHING.add(key)
    try:
        resolved = await resolve_media_path(state, path)
        if resolved.needs_115:
            await state.resolver.resolve(resolved, user_agent)
    except Exception as exc:  # noqa: BLE001 - warming must never disturb a request
        log.debug("prefetch for %s did not warm: %s", path, exc)
    finally:
        _PREFETCHING.discard(key)


async def handle_playback_info(state: ProxyState, request: Request, item_id: str) -> Response:
    query = dict(request.query_params)
    api_key = api_key_from(query, request.headers)
    device_id = device_id_from(query, request.headers)
    try:
        info = await _fetch_playback_info(state, request, item_id)
    except EmbyError as exc:
        log.error("PlaybackInfo for %s failed: %s", item_id, exc)
        return Response(
            content=json.dumps({"error": str(exc)}).encode(),
            status_code=exc.status,
            media_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )
    rewritten = rewrite_playback_info(
        info,
        item_id=item_id,
        base_url=_base_url(state, request, query),
        api_key=api_key,
        device_id=device_id,
    )
    if state.app_config.server.prefetch:
        user_agent = request.headers.get("user-agent", "")
        for source in (rewritten.get("MediaSources") or [])[:2]:
            path = media_source_path(source)
            if path:
                asyncio.create_task(prefetch_source(state, path, user_agent))

    return Response(
        content=json.dumps(rewritten).encode(),
        status_code=200,
        media_type="application/json",
        headers={"Cache-Control": "no-store", "Access-Control-Allow-Origin": "*"},
    )


async def passthrough(
    state: ProxyState, request: Request, *, force_buffer: bool = False
) -> Response:
    url = f"{state.server.host}{request.url.path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    headers = _upstream_headers(request)
    body = await request.body()
    upstream_request = state.client.build_request(
        request.method, url, headers=headers, content=body or None
    )
    if force_buffer:
        response = await state.client.send(upstream_request)
        content = response.content
        out_headers = _filtered_headers(response.headers)
        out_headers.pop("content-length", None)
        if BASEHTMLPLAYER_RE.search(request.url.path) and state.app_config.server.rewrite_cors_js:
            text = content.decode(response.encoding or "utf-8", "replace")
            patched = CORS_JS_PATTERN.sub("null", text)
            if patched != text:
                content = patched.encode("utf-8")
                out_headers.pop("content-encoding", None)
                out_headers["content-length"] = str(len(content))
        return Response(
            content=content, status_code=response.status_code, headers=out_headers
        )

    response = await state.client.send(upstream_request, stream=True)
    out_headers = _filtered_headers(response.headers)
    location = out_headers.get("location")
    if location and location.startswith(state.server.host):
        out_headers["location"] = str(request.base_url).rstrip("/") + location[len(state.server.host) :]

    async def body_iter() -> AsyncIterator[bytes]:
        try:
            async for chunk in response.aiter_raw():
                yield chunk
        finally:
            await response.aclose()

    return StreamingResponse(
        body_iter(), status_code=response.status_code, headers=out_headers
    )


@dataclass(slots=True)
class Ctx:
    """What a running listener reads. Swapping ``state`` reloads it in place:
    the ASGI app keeps working while the configuration behind it changes."""

    state: ProxyState
    app_config: AppConfig

    def swap(self, state: ProxyState, app_config: AppConfig) -> ProxyState:
        previous, self.state, self.app_config = self.state, state, app_config
        return previous


@asynccontextmanager
async def _lifespan(ctx: Ctx):
    yield
    await ctx.state.client.aclose()
    await ctx.state.resolver.aclose()


def create_app(ctx: Ctx, server: EmbyServerConfig) -> FastAPI:
    app = FastAPI(
        title=f"pley302:{server.name}",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lambda _app: _lifespan(ctx),
    )
    app.state.ctx = ctx

    @app.api_route(
        "/{full_path:path}",
        methods=["GET", "HEAD", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    )
    async def dispatch(request: Request, full_path: str) -> Response:
        state = ctx.state
        path = "/" + full_path
        # Every request that reaches us, so a failure can be pinned to a hop.
        log.info(
            "%s %s ua=%r range=%r",
            request.method,
            path[:120],
            request.headers.get("user-agent", "")[:40],
            request.headers.get("range", ""),
        )
        if request.method.upper() == "OPTIONS":
            return Response(status_code=204, headers=dict(REDIRECT_HEADERS))
        match = PLAYBACKINFO_RE.match(path)
        if match:
            return await handle_playback_info(state, request, match.group("item"))
        direct = DIRECT_RE.match(path)
        if direct:
            return await handle_direct(state, request, direct.group("path"))
        relay = RELAY_RE.match(path)
        if relay:
            return await handle_relay(state, request, relay.group("path"))
        if STREAM_RE.match(path) or DOWNLOAD_RE.match(path):
            item_id = (STREAM_RE.match(path) or DOWNLOAD_RE.match(path)).group("item")
            return await handle_play(state, request, item_id)
        if BASEHTMLPLAYER_RE.search(path) and ctx.app_config.server.rewrite_cors_js:
            try:
                return await passthrough(state, request, force_buffer=True)
            except httpx.HTTPError as exc:
                log.warning("patching basehtmlplayer.js failed: %s", exc)
        return await passthrough(state, request)

    return app
