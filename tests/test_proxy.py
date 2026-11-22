from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from pley302.app import Runtime, create_admin_app
from pley302.cache import RedirectCache, TTLCache
from pley302.config import AppConfig, EmbyServerConfig, P115Settings, ServerSettings
from pley302.emby import EmbyUpstream
from pley302.index import StrmIndex
from pley302.mapping import PathMapper
from pley302.proxy import Ctx, ProxyState, create_app
from pley302.rewrite import rewrite_playback_info

MEDIA_PATH = "/CloudNAS/CloudDrive/115open/Media/阿甘正传/阿甘正传.mkv"


def make_upstream_app(path: str = MEDIA_PATH) -> FastAPI:
    app = FastAPI()

    def answer() -> JSONResponse:
        return JSONResponse(
            {
                "PlaySessionId": "ps-1",
                "MediaSources": [
                    {
                        "Id": "src-1",
                        "Name": "阿甘正传",
                        "Path": path,
                        "Container": "mkv",
                        "Protocol": "File",
                        "SupportsDirectPlay": True,
                        "SupportsDirectStream": True,
                    }
                ],
            }
        )

    @app.get("/Items/{item_id}/PlaybackInfo")
    async def playback_get(item_id: str) -> JSONResponse:
        return answer()

    @app.post("/Items/{item_id}/PlaybackInfo")
    async def playback_post(item_id: str, request: Request) -> JSONResponse:
        return answer()

    @app.get("/videos/{item_id}/stream")
    async def stream(item_id: str) -> PlainTextResponse:
        return PlainTextResponse("PROXIED-FROM-EMBY")

    @app.head("/videos/{item_id}/stream")
    async def stream_head(item_id: str) -> PlainTextResponse:
        return PlainTextResponse("")

    @app.get("/System/Info/Public")
    async def public_info() -> JSONResponse:
        return JSONResponse({"ServerName": "mock-emby"})

    @app.api_route("/cdn/{name}", methods=["GET", "HEAD"])
    async def cdn(name: str) -> PlainTextResponse:
        return PlainTextResponse("CDN-BYTES", headers={"Content-Type": "video/x-matroska"})

    return app


class StubResolver:
    def __init__(self, url: str | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.url = url

    async def resolve(self, target, user_agent: str, *, fresh: bool = False) -> str:
        self.calls.append((target.value, user_agent))
        if self.url:
            return self.url
        return f"https://cdn115.example/fetch?file={target.value.strip('/')}&f=1"

    async def lease(self, target, user_agent: str) -> str:
        return await self.resolve(target, user_agent, fresh=True)

    async def release(self, url: str) -> None:
        return None

    def slot(self, file_key: str):
        return contextlib.nullcontext()

    def clear(self) -> None:
        self.calls.clear()

    def close(self) -> None:
        pass


def make_state(
    resolver: StubResolver,
    *,
    upstream_path: str = MEDIA_PATH,
    mapping: str = "/CloudNAS/CloudDrive/115open/Media => /Media",
    local_filepath: str = "",
    direct_path: str = "",
    library_roots: str = "",
) -> tuple[ProxyState, AppConfig, EmbyServerConfig]:
    server_cfg = EmbyServerConfig.from_dict(
        {
            "name": "test",
            "host": "http://emby:8096",
            "proxy_port": 8098,
            "filepath_mapping": mapping,
            "local_filepath": local_filepath,
            "direct_path": direct_path,
            "library_roots": library_roots,
        }
    )
    config = AppConfig(
        server=ServerSettings(redirect_cache_ttl=60, mediasources_cache_ttl=60),
        p115=P115Settings(),
        emby_servers=[server_cfg],
    )
    transport = httpx.ASGITransport(app=make_upstream_app(upstream_path))
    client = httpx.AsyncClient(transport=transport, base_url="http://emby:8096")
    state = ProxyState(
        app_config=config,
        server=server_cfg,
        mapper=PathMapper(server_cfg),
        resolver=resolver,  # duck-typed
        redirects=RedirectCache.with_ttl(60),
        playback=TTLCache(60),
        upstream=EmbyUpstream(server_cfg.host, client=client),
        client=client,
        index=StrmIndex(server_cfg.library_roots),
    )
    return state, config, server_cfg


def call_proxy(config: AppConfig, server_cfg: EmbyServerConfig, state: ProxyState, method: str, url: str, **kwargs):
    app = create_app(Ctx(state=state, app_config=config), server_cfg)

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
            return await client.request(method, url, **kwargs)

    return asyncio.run(run())


def test_playback_info_is_rewritten_to_the_proxy():
    resolver = StubResolver()
    state, config, server_cfg = make_state(resolver)
    response = call_proxy(
        config, server_cfg, state, "GET", "/Items/42/PlaybackInfo?api_key=k&DeviceId=dev1"
    )
    assert response.status_code == 200
    body = response.json()
    source = body["MediaSources"][0]
    assert source["Id"] == "src-1"
    assert source["Protocol"] == "Http"
    assert source["IsRemote"] is True
    assert source["Path"].startswith("http://proxy.test/videos/42/stream?")
    assert "MediaSourceId=src-1" in source["Path"]
    assert "api_key=k" in source["Path"]
    assert source["DirectStreamUrl"] == source["Path"]
    assert source["DirectPlayUrl"] == source["Path"]


def test_play_request_answers_302_with_the_cloud_url():
    resolver = StubResolver()
    state, config, server_cfg = make_state(resolver)
    response = call_proxy(
        config,
        server_cfg,
        state,
        "GET",
        "/videos/42/stream?MediaSourceId=src-1&Static=true&DeviceId=dev1",
        headers={"User-Agent": "Infuse/7.8"},
    )
    assert response.status_code == 302
    assert response.headers["location"] == (
        "https://cdn115.example/fetch?file=Media/%E9%98%BF%E7%94%98%E6%AD%A3%E4%BC%A0/%E9%98%BF%E7%94%98%E6%AD%A3%E4%BC%A0.mkv&f=1"
    )
    assert response.headers["access-control-allow-origin"] == "*"
    assert resolver.calls and resolver.calls[0][1] == "Infuse/7.8"


def test_play_requests_ask_the_resolver_every_time():
    # Reuse is the resolver's business (see test_resolver_*); the proxy must
    # hand each play request a URL and answer 302.
    resolver = StubResolver()
    state, config, server_cfg = make_state(resolver)
    query = "/videos/42/stream?MediaSourceId=src-1&Static=true&DeviceId=dev1"
    for _ in range(2):
        response = call_proxy(config, server_cfg, state, "GET", query, headers={"User-Agent": "Infuse/7.8"})
        assert response.status_code == 302
    assert len(resolver.calls) == 2
    assert state.redirects.snapshot()


def test_ignored_path_is_passed_through_untouched():
    resolver = StubResolver()
    state, config, server_cfg = make_state(
        resolver, upstream_path="/mnt/ignore/a.mkv", direct_path="/mnt/ignore"
    )
    response = call_proxy(
        config, server_cfg, state, "GET", "/videos/42/stream?MediaSourceId=src-1"
    )
    assert response.status_code == 200
    assert response.text == "PROXIED-FROM-EMBY"
    assert resolver.calls == []


def test_local_path_is_served_with_ranges(tmp_path: Path):
    media = tmp_path / "movie.mkv"
    media.write_bytes(b"0123456789")
    resolver = StubResolver()
    state, config, server_cfg = make_state(
        resolver, upstream_path=str(media), local_filepath=str(tmp_path)
    )
    full = call_proxy(config, server_cfg, state, "GET", "/videos/42/stream?MediaSourceId=src-1")
    assert full.status_code == 200
    assert full.content == b"0123456789"
    part = call_proxy(
        config,
        server_cfg,
        state,
        "GET",
        "/videos/42/stream?MediaSourceId=src-1",
        headers={"Range": "bytes=2-5"},
    )
    assert part.status_code == 206
    assert part.content == b"2345"
    assert part.headers["content-range"] == "bytes 2-5/10"


def test_unrelated_requests_are_proxied():
    resolver = StubResolver()
    state, config, server_cfg = make_state(resolver)
    response = call_proxy(config, server_cfg, state, "GET", "/System/Info/Public")
    assert response.status_code == 200
    assert response.json() == {"ServerName": "mock-emby"}


def test_rewrite_keeps_media_streams():
    info = {
        "MediaSources": [
            {
                "Id": "abc",
                "Path": "/Media/x.mkv",
                "Container": "mkv",
                "MediaStreams": [{"Index": 0, "Type": "Video"}],
            }
        ]
    }
    out = rewrite_playback_info(info, item_id="9", base_url="http://proxy", api_key="", device_id="")
    assert out["MediaSources"][0]["MediaStreams"] == [{"Index": 0, "Type": "Video"}]
    assert out["MediaSources"][0]["Container"] == "mkv"
    assert info["MediaSources"][0]["Path"] == "/Media/x.mkv"  # input untouched


def test_admin_cache_endpoints_have_the_listed_shape(tmp_path):
    from pley302.app import Runtime

    resolver = StubResolver()
    state, config, server_cfg = make_state(resolver)
    call_proxy(
        config, server_cfg, state, "GET", "/videos/42/stream?MediaSourceId=src-1&DeviceId=dev1"
    )
    runtime = Runtime(str(tmp_path / "config.yaml"), config)
    runtime.ctxs[8098] = Ctx(state=state, app_config=config)
    app = create_admin_app(runtime)

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://admin") as client:
            listing = await client.get("/api/v1/reverse/cache/url_cache")
            cleared = await client.post("/api/v1/reverse/cache/clear_all_url_cache")
            after = await client.get("/api/v1/reverse/cache/url_cache")
            return listing, cleared, after

    listing, cleared, after = asyncio.run(run())
    payload = listing.json()
    assert payload["success"] is True
    assert payload["data"]["cache_expire"] == 60
    entry = next(iter(payload["data"]["cache"].values()))
    assert entry["redirect_url"].startswith("https://cdn115.example/")
    assert entry["device_id"] == "dev1"
    assert json.dumps(payload)  # serialisable
    assert cleared.json()["success"] is True
    assert after.json()["data"]["cache"] == {}


def test_runtime_only_serves_enabled_ports(tmp_path):
    from pley302.config import save_config

    config = AppConfig.from_dict(
        {
            "emby_servers": [
                {"name": "a", "host": "http://a:8096", "proxy_port": 8098},
                {"name": "b", "host": "http://b:8096", "proxy_port": 8099, "switch": False},
            ]
        }
    )
    path = tmp_path / "config.yaml"
    save_config(config, path)
    runtime = Runtime(str(path), config)
    assert sorted(runtime._wanted()) == [8098]


def test_strm_item_named_only_by_file_is_resolved_from_the_library(tmp_path: Path):
    # Pley reports `/越狱特别篇….strm`, never the folder; the .strm file holds the
    # 115 path, which still has to go through filepath_mapping.
    folder = tmp_path / "电影" / "欧美电影" / "越狱特别篇：最后一越 (2009) {tmdb-176241}"
    folder.mkdir(parents=True)
    strm = folder / "越狱特别篇：最后一越 (2009) - 1080p.BluRay.Remux.H.264.strm"
    strm.write_text(
        "/CloudNAS/CloudDrive/115open(dy)/媒体库/电影/欧美电影/"
        "越狱特别篇：最后一越 (2009) {tmdb-176241}/越狱特别篇：最后一越 (2009) - 1080p.BluRay.Remux.H.264.mkv\n",
        encoding="utf-8",
    )
    resolver = StubResolver()
    state, config, server_cfg = make_state(
        resolver,
        upstream_path="/" + strm.name,
        mapping="/CloudNAS/CloudDrive/115open(dy) => /",
        library_roots=str(tmp_path),
    )
    response = call_proxy(
        config, server_cfg, state, "GET", "/videos/7/stream?MediaSourceId=src-1", headers={"User-Agent": "Infuse/7.8"}
    )
    assert response.status_code == 302
    assert resolver.calls == [
        (
            "/媒体库/电影/欧美电影/越狱特别篇：最后一越 (2009) {tmdb-176241}"
            "/越狱特别篇：最后一越 (2009) - 1080p.BluRay.Remux.H.264.mkv",
            "Infuse/7.8",
        )
    ]


def test_strm_holding_an_address_is_used_directly(tmp_path: Path):
    (tmp_path / "Film.strm").write_text(
        "https://openlist.example.com/d/115open/Film.mkv|User-Agent=Foo\n", encoding="utf-8"
    )
    resolver = StubResolver()
    state, config, server_cfg = make_state(
        resolver, upstream_path="/Film.strm", library_roots=str(tmp_path)
    )
    response = call_proxy(config, server_cfg, state, "GET", "/videos/7/stream?MediaSourceId=src-1")
    assert response.status_code == 302
    assert response.headers["location"] == "https://openlist.example.com/d/115open/Film.mkv"
    assert resolver.calls == []


def test_direct_route_answers_302_for_strm_files():
    resolver = StubResolver()
    state, config, server_cfg = make_state(resolver, mapping="/CloudNAS/CloudDrive/115open(dy) => /")
    encoded = "/%E5%AA%92%E4%BD%93%E5%BA%93/demo.mkv"  # /媒体库/demo.mkv
    response = call_proxy(
        config, server_cfg, state, "GET", "/d" + encoded, headers={"User-Agent": "Infuse/7.8"}
    )
    assert response.status_code == 302
    assert resolver.calls == [("/媒体库/demo.mkv", "Infuse/7.8")]


def test_strm_pointing_at_our_own_direct_route_resolves_to_115(tmp_path: Path):
    # After scripts/rewrite_strm.py the .strm holds our own /d/ address on a
    # private host; a player could not reach it, so it is resolved here.
    encoded = "/CloudNAS/CloudDrive/115open(dy)/%E5%AA%92%E4%BD%93%E5%BA%93/demo.mkv"
    (tmp_path / "Film.strm").write_text(f"http://10.1.2.3:8096/d{encoded}\n", encoding="utf-8")
    resolver = StubResolver()
    state, config, server_cfg = make_state(
        resolver,
        upstream_path="/Film.strm",
        mapping="/CloudNAS/CloudDrive/115open(dy) => /",
        library_roots=str(tmp_path),
    )
    response = call_proxy(
        config, server_cfg, state, "GET", "/videos/7/stream?MediaSourceId=src-1", headers={"User-Agent": "Infuse/7.8"}
    )
    assert response.status_code == 302
    assert resolver.calls == [("/媒体库/demo.mkv", "Infuse/7.8")]


def test_bad_115_url_is_resolved_again_then_fails_cleanly():
    import pytest as _pytest

    from pley302.mapping import ResolvedPath
    from pley302.p115 import P115Error, P115Resolver

    calls: list[str] = []

    async def verifier(url: str, ua: str) -> bool:
        return False

    resolver = P115Resolver(
        P115Settings(), cache_ttl=60, verifier=verifier, verify=True
    )
    resolver._resolve_blocking = lambda target, ua: (calls.append(target.value) or f"https://cdn/{len(calls)}")

    with _pytest.raises(P115Error):
        asyncio.run(resolver.resolve(ResolvedPath(kind="path", value="/a/b.mkv", source="/a/b.mkv"), "UA"))
    assert calls == ["/a/b.mkv", "/a/b.mkv"]  # asked again with a fresh URL


def test_good_115_url_is_kept_and_cached():
    from pley302.mapping import ResolvedPath
    from pley302.p115 import P115Resolver

    calls: list[str] = []

    async def verifier(url: str, ua: str) -> bool:
        return True

    resolver = P115Resolver(P115Settings(), cache_ttl=60, verifier=verifier, verify=True)
    resolver._resolve_blocking = lambda target, ua: (calls.append(target.value) or "https://cdn/x")

    target = ResolvedPath(kind="path", value="/a/b.mkv", source="/a/b.mkv")
    url = asyncio.run(resolver.resolve(target, "UA"))
    assert url == "https://cdn/x"
    assert asyncio.run(resolver.resolve(target, "UA")) == "https://cdn/x"
    assert calls == ["/a/b.mkv"]


class UnusableResolver(StubResolver):
    """Hands out a URL that will not open, the way 115's edge sometimes does."""

    def __init__(self, url: str = "https://cdn/cdn/file") -> None:
        super().__init__()
        self.url = url

    async def resolve(self, target, user_agent: str, *, fresh: bool = False) -> str:
        from pley302.p115 import P115Unusable

        raise P115Unusable("115 handed out a URL that would not open", self.url)

    async def lease(self, target, user_agent: str) -> str:
        return await self.resolve(target, user_agent, fresh=True)

    async def release(self, url: str) -> None:
        return None

    def slot(self, file_key: str):
        return contextlib.nullcontext()


def test_unusable_115_url_is_relayed_instead_of_302():
    state, config, server_cfg = make_state(UnusableResolver())
    response = call_proxy(
        config, server_cfg, state, "GET", "/videos/42/stream?MediaSourceId=src-1", headers={"User-Agent": "Infuse/7.8"}
    )
    assert response.status_code == 200
    assert response.text == "CDN-BYTES"
    assert response.headers["content-type"] == "video/x-matroska"


def test_relay_route_sends_bytes_instead_of_redirecting():
    state, config, server_cfg = make_state(StubResolver("https://cdn/cdn/file"), mapping="/c => /")
    response = call_proxy(
        config,
        server_cfg,
        state,
        "GET",
        "/r/%2Fc%2Ffile.mkv",
        headers={"User-Agent": "Pley/0.1.14", "Range": "bytes=0-1"},
    )
    assert response.status_code == 200
    assert response.text == "CDN-BYTES"


def test_strm_pointing_at_the_relay_route_still_redirects_a_player(tmp_path):
    encoded = "/CloudNAS/CloudDrive/115open(dy)/%E5%AA%92%E4%BD%93%E5%BA%93/demo.mkv"
    (tmp_path / "Film.strm").write_text(f"http://10.1.2.3:8096/r{encoded}\n", encoding="utf-8")
    resolver = StubResolver()
    state, config, server_cfg = make_state(
        resolver,
        upstream_path="/Film.strm",
        mapping="/CloudNAS/CloudDrive/115open(dy) => /",
        library_roots=str(tmp_path),
    )
    response = call_proxy(config, server_cfg, state, "GET", "/videos/7/stream?MediaSourceId=src-1")
    assert response.status_code == 302
    assert [call[0] for call in resolver.calls] == ["/媒体库/demo.mkv"]


def test_resolver_reuses_a_fresh_url_up_to_the_limit():
    from pley302.mapping import ResolvedPath
    from pley302.p115 import P115Resolver

    mints: list[str] = []

    async def verifier(url: str, ua: str) -> bool:
        return True

    resolver = P115Resolver(
        P115Settings(), verifier=verifier, verify=True, url_reuse_seconds=60, url_share_limit=2
    )
    resolver._resolve_blocking = lambda target, ua: (mints.append(target.value) or f"https://cdn/{len(mints)}")

    target = ResolvedPath(kind="path", value="/a/b.mkv", source="/a/b.mkv")
    first = asyncio.run(resolver.resolve(target, "UA"))
    second = asyncio.run(resolver.resolve(target, "UA"))
    third = asyncio.run(resolver.resolve(target, "UA"))
    # Back-to-back: one mint, one share, then a fresh URL for the third.
    assert first == second == "https://cdn/1"
    assert third == "https://cdn/2"
    assert len(mints) == 2
    # Spaced out in time, the same URL is reused again.
    resolver._urls[("path", "/a/b.mkv", "UA")]["last"] -= 10
    assert asyncio.run(resolver.resolve(target, "UA")) == "https://cdn/2"
    assert len(mints) == 2
    # A different User-Agent never shares: 115 binds the URL to it.
    asyncio.run(resolver.resolve(target, "Other"))
    assert len(mints) == 3


def test_resolver_caches_the_file_id_for_a_path():
    from pley302.mapping import ResolvedPath
    from pley302.p115 import P115Resolver

    lookups: list[str] = []

    class FakeFS:
        def get_id(self, path: str) -> int:
            lookups.append(path)
            return 42

    class FakeClient:
        fs = FakeFS()

        def download_url(self, pickcode, **kwargs):
            return f"https://cdn/{pickcode}"

    resolver = P115Resolver(P115Settings(), verify=False, file_id_ttl=60)
    resolver._client = FakeClient()
    target = ResolvedPath(kind="path", value="/a/b.mkv", source="/a/b.mkv")
    assert asyncio.run(resolver.resolve(target, "UA", fresh=True)) == "https://cdn/42"
    assert asyncio.run(resolver.resolve(target, "UA", fresh=True)) == "https://cdn/42"
    assert lookups == ["/a/b.mkv"]  # the walk happens once


def test_prefetch_warms_the_resolver_before_the_play_request():
    from pley302.proxy import prefetch_source

    resolver = StubResolver()
    state, config, server_cfg = make_state(resolver, mapping="/CloudNAS/CloudDrive/115open(dy) => /")
    asyncio.run(prefetch_source(state, "/CloudNAS/CloudDrive/115open(dy)/媒体库/x.mkv", "Infuse/7.8"))
    assert resolver.calls == [("/媒体库/x.mkv", "Infuse/7.8")]


def test_admin_panel_page_is_served(tmp_path):
    from pley302.app import Runtime

    state, config, _server_cfg = make_state(StubResolver())
    runtime = Runtime(str(tmp_path / "config.yaml"), config)
    runtime.ctxs[8098] = Ctx(state=state, app_config=config)
    app = create_admin_app(runtime)

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://admin") as client:
            return await client.get("/")

    response = asyncio.run(run())
    assert response.status_code == 200
    assert "pley302" in response.text
    assert "/api/v1/reverse/cache/url_cache" in response.text


def test_upstream_404_is_passed_on_not_masked_as_502():
    import pytest as _pytest

    from pley302.emby import EmbyError

    state, config, server_cfg = make_state(StubResolver())

    async def refuse(*args, **kwargs):
        raise EmbyError("PlaybackInfo answered 404: not found", status=404)

    state.upstream.playback_info = refuse
    response = call_proxy(
        config, server_cfg, state, "GET", "/Items/7/PlaybackInfo?api_key=k&DeviceId=d"
    )
    assert response.status_code == 404
    assert "404" in response.text
