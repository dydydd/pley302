"""Wiring: one proxy listener per configured Emby server, plus the admin app.

The panel edits the same file the service reads, so a saved configuration and
a restart land on the same state. Which is why saving does not restart the
process: listeners are added, dropped or reloaded in place, and the ones that
stay keep serving while their settings are swapped underneath.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from dataclasses import dataclass
from typing import Any

import httpx
import uvicorn
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .admin_ui import INDEX_HTML
from .cache import RedirectCache, TTLCache
from .config import (
    AppConfig,
    EmbyServerConfig,
    MappingRule,
    P115Settings,
    ServerSettings,
    load_config,
    save_config,
    settings_view,
)
from .emby import EmbyUpstream
from .index import StrmIndex
from .mapping import PathMapper
from .p115 import P115Resolver
from .proxy import Ctx, ProxyState, create_app

log = logging.getLogger("pley302")

APP_VERSION = "1.1.0"


# -- state -----------------------------------------------------------------


def build_state(config: AppConfig, server: EmbyServerConfig) -> ProxyState:
    return ProxyState(
        app_config=config,
        server=server,
        mapper=PathMapper(server),
        resolver=P115Resolver(
            config.p115,
            cache_ttl=config.server.redirect_cache_ttl,
            max_entries=config.server.max_cache_entries,
            verify=config.server.verify_redirect,
            file_id_ttl=config.server.file_id_ttl,
            url_reuse_seconds=config.server.url_reuse_seconds,
            url_share_limit=config.server.url_share_limit,
            file_id_cache_file=config.server.file_id_cache_file,
        ),
        redirects=RedirectCache.with_ttl(
            config.server.redirect_cache_ttl, config.server.max_cache_entries
        ),
        playback=TTLCache(config.server.mediasources_cache_ttl, config.server.max_cache_entries),
        upstream=EmbyUpstream(server.host),
        client=httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=False
        ),
        index=StrmIndex(server.library_roots),
    )


async def _close_state(state: ProxyState) -> None:
    try:
        await state.client.aclose()
        await state.resolver.aclose()
    except Exception:  # pragma: no cover - shutdown is best effort
        pass


@dataclass(slots=True)
class Saved:
    """What a save changed, for the answer the panel shows."""

    ports: list[int]
    removed: list[int]
    admin_port_changed: bool


class Runtime:
    def __init__(self, config_path: str, config: AppConfig) -> None:
        self.config_path = config_path
        self.config = config
        self.ctxs: dict[int, Ctx] = {}
        self.listeners: dict[int, uvicorn.Server] = {}
        self.tasks: dict[int, asyncio.Task] = {}
        self.admin_app = create_admin_app(self)
        self.admin_listener: uvicorn.Server | None = None

    # -- listeners --------------------------------------------------------
    def _wanted(self) -> dict[int, EmbyServerConfig]:
        return {server.proxy_port: server for server in self.config.enabled_servers()}

    async def _start_port(self, server: EmbyServerConfig) -> None:
        state = build_state(self.config, server)
        try:
            state.index.refresh()
        except Exception as exc:  # pragma: no cover - a missing folder is fine
            log.warning("could not index %s: %s", server.library_roots, exc)
        ctx = Ctx(state=state, app_config=self.config)
        app = create_app(ctx, server)
        listener = uvicorn.Server(
            uvicorn.Config(
                app,
                host=self.config.server.host,
                port=server.proxy_port,
                log_level=self.config.server.log_level.lower(),
                access_log=False,
            )
        )
        self.ctxs[server.proxy_port] = ctx
        self.listeners[server.proxy_port] = listener
        self.tasks[server.proxy_port] = asyncio.create_task(
            listener.serve(), name=f"pley302:{server.proxy_port}"
        )
        log.info("listening for %s on %s:%s", server.name, self.config.server.host, server.proxy_port)

    async def _stop_port(self, port: int) -> None:
        listener = self.listeners.pop(port, None)
        task = self.tasks.pop(port, None)
        ctx = self.ctxs.pop(port, None)
        if listener is not None:
            listener.should_exit = True
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()
        if ctx is not None:
            await _close_state(ctx.state)
        log.info("stopped listening on port %s", port)

    async def _sync(self) -> Saved:
        wanted = self._wanted()
        removed = [port for port in list(self.listeners) if port not in wanted]
        for port in removed:
            await self._stop_port(port)
        for port, server in wanted.items():
            if port in self.ctxs:
                state = build_state(self.config, server)
                old = self.ctxs[port].swap(state, self.config)
                asyncio.create_task(_close_state(old))
            else:
                await self._start_port(server)
        return Saved(
            ports=sorted(self.listeners),
            removed=removed,
            admin_port_changed=False,
        )

    # -- lifecycle --------------------------------------------------------
    async def start(self) -> None:
        await self._sync()
        self.admin_listener = uvicorn.Server(
            uvicorn.Config(
                self.admin_app,
                host=self.config.server.host,
                port=self.config.server.admin_port,
                log_level=self.config.server.log_level.lower(),
                access_log=False,
            )
        )
        log.info("admin panel on %s:%s", self.config.server.host, self.config.server.admin_port)
        await self.admin_listener.serve()

    async def apply(self, config: AppConfig) -> Saved:
        previous = self.config
        save_config(config, self.config_path)
        self.config = config
        saved = await self._sync()
        saved.admin_port_changed = previous.server.admin_port != config.server.admin_port
        if saved.admin_port_changed:
            log.warning("admin_port changed; it applies after a restart")
        return saved

    async def stop(self) -> None:
        if self.admin_listener is not None:
            self.admin_listener.should_exit = True
        for port in list(self.listeners):
            await self._stop_port(port)


# -- panel API -------------------------------------------------------------


def _authorized(config: AppConfig, request: Request) -> bool:
    token = config.server.admin_token
    if not token:
        return True
    header = request.headers.get("authorization") or ""
    supplied = header.removeprefix("Bearer ").strip()
    if supplied == token:
        return True
    return request.query_params.get("token", "") == token


def _as_lines(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "\n".join(str(item) for item in value)
    return str(value)


def config_from_payload(payload: dict[str, Any], current: AppConfig) -> AppConfig:
    """Build the configuration the panel asked for, keeping what it left blank."""
    server_data = dict(payload.get("server") or {})
    server = ServerSettings(
        host=current.server.host,
        admin_port=int(server_data.get("admin_port") or current.server.admin_port),
        admin_token=current.server.admin_token,
        redirect_cache_ttl=int(
            server_data.get("redirect_cache_ttl", current.server.redirect_cache_ttl)
        ),
        file_id_ttl=int(server_data.get("file_id_ttl", current.server.file_id_ttl)),
        file_id_cache_file=str(
            server_data.get("file_id_cache_file", current.server.file_id_cache_file) or ""
        ),
        url_reuse_seconds=int(
            server_data.get("url_reuse_seconds", current.server.url_reuse_seconds)
        ),
        url_share_limit=int(server_data.get("url_share_limit", current.server.url_share_limit)),
        mediasources_cache_ttl=int(
            server_data.get("mediasources_cache_ttl", current.server.mediasources_cache_ttl)
        ),
        rewrite_cors_js=bool(server_data.get("rewrite_cors_js", current.server.rewrite_cors_js)),
        forward_client_ua=current.server.forward_client_ua,
        verify_redirect=bool(server_data.get("verify_redirect", current.server.verify_redirect)),
        fallback_proxy=bool(server_data.get("fallback_proxy", current.server.fallback_proxy)),
        prefetch=bool(server_data.get("prefetch", current.server.prefetch)),
        max_cache_entries=current.server.max_cache_entries,
        log_level=current.server.log_level,
        access_log=current.server.access_log,
    )
    if server_data.get("admin_token"):
        server.admin_token = str(server_data["admin_token"])

    p115_data = dict(payload.get("p115") or {})
    p115 = P115Settings(
        cookie=current.p115.cookie,
        user_agent=str(p115_data.get("user_agent") or current.p115.user_agent),
        api=str(p115_data.get("api") or current.p115.api),
        timeout=current.p115.timeout,
    )
    cookie = str(p115_data.get("cookie") or "").strip()
    if cookie and cookie != "__keep__":
        p115.cookie = cookie

    servers: list[EmbyServerConfig] = []
    entries = payload.get("emby_servers") if "emby_servers" in payload else None
    if entries is None:
        # A payload that only changes the cookie or a cache knob keeps the
        # servers it was given.
        return AppConfig(server=server, p115=p115, emby_servers=list(current.emby_servers))
    for item in entries or []:
        if not isinstance(item, dict):
            continue
        rules: list[MappingRule] = []
        for line in _as_lines(item.get("filepath_mapping")).splitlines():
            if not line.strip():
                continue
            rules.append(MappingRule.parse(line.strip()))
        servers.append(
            EmbyServerConfig(
                name=str(item.get("name") or "emby").strip(),
                host=str(item.get("host") or "").strip().rstrip("/"),
                public_address=str(item.get("public_address") or "").strip().rstrip("/"),
                proxy_port=int(item.get("proxy_port") or 8098),
                switch=bool(item.get("switch", True)),
                filepath_mapping=rules,
                local_filepath=[
                    line.strip().rstrip("/")
                    for line in _as_lines(item.get("local_filepath")).splitlines()
                    if line.strip()
                ],
                direct_path=[
                    line.strip().rstrip("/")
                    for line in _as_lines(item.get("direct_path")).splitlines()
                    if line.strip()
                ],
                library_roots=[
                    line.strip().rstrip("/")
                    for line in _as_lines(item.get("library_roots")).splitlines()
                    if line.strip()
                ],
            )
        )
    if not servers:
        raise ValueError("至少需要一台 Emby/Pley 服务器")
    for item in servers:
        if not item.host.startswith(("http://", "https://")):
            raise ValueError(f"{item.name}: host 要以 http:// 或 https:// 开头")
        if not 1 <= item.proxy_port <= 65535:
            raise ValueError(f"{item.name}: 端口不合法")
    ports = [item.proxy_port for item in servers if item.switch]
    if len(ports) != len(set(ports)):
        raise ValueError("两台启用中的服务器用了同一个反代端口")
    return AppConfig(server=server, p115=p115, emby_servers=servers)


async def test_cookie(cookie: str, user_agent: str) -> dict[str, Any]:
    """Ask 115 who this cookie is, in a worker thread."""

    def probe() -> dict[str, Any]:
        import p115client

        client = p115client.P115Client(cookie)
        user_id = getattr(client, "user_id", None)
        roots = [str(name) for name in list(client.fs.listdir("/"))[:8]]
        return {"user_id": user_id, "roots": roots}

    try:
        return {"ok": True, **(await asyncio.to_thread(probe))}
    except Exception as exc:  # noqa: BLE001 - the answer carries the reason
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}


def create_admin_app(runtime: Runtime) -> FastAPI:
    app = FastAPI(title="pley302 admin", docs_url=None, redoc_url=None, openapi_url=None)

    def config() -> AppConfig:
        return runtime.config

    @app.get("/", response_class=HTMLResponse)
    async def panel() -> HTMLResponse:
        return HTMLResponse(INDEX_HTML)

    @app.get("/api/v1/system/app_version")
    async def app_version() -> dict[str, Any]:
        return {"success": True, "data": {"app_version": APP_VERSION}}

    @app.get("/api/v1/health")
    async def health() -> dict[str, Any]:
        return {
            "success": True,
            "data": {
                "version": APP_VERSION,
                "servers": [
                    {
                        "name": ctx.state.server.name,
                        "host": ctx.state.server.host,
                        "proxy_port": port,
                        "redirects": len(ctx.state.redirects.snapshot()),
                    }
                    for port, ctx in sorted(runtime.ctxs.items())
                ],
            },
        }

    @app.get("/api/v1/settings")
    async def get_settings(request: Request) -> JSONResponse:
        if not _authorized(config(), request):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return JSONResponse({"success": True, "data": settings_view(config())})

    @app.post("/api/v1/settings")
    async def put_settings(request: Request) -> JSONResponse:
        if not _authorized(config(), request):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("body 要是一个对象")
            new_config = config_from_payload(payload, config())
        except (ValueError, TypeError, yaml.YAMLError) as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=400)
        try:
            saved = await runtime.apply(new_config)
        except OSError as exc:
            return JSONResponse(
                {"success": False, "error": f"配置写入失败: {exc}"}, status_code=500
            )
        log.info("settings saved; listening on %s", saved.ports)
        return JSONResponse(
            {
                "success": True,
                "data": {
                    "ports": saved.ports,
                    "removed": saved.removed,
                    "admin_port_changed": saved.admin_port_changed,
                },
            }
        )

    @app.post("/api/v1/p115/test")
    async def p115_test(request: Request) -> JSONResponse:
        if not _authorized(config(), request):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        payload: dict[str, Any] = {}
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001 - an empty body means "test the saved one"
            payload = {}
        cookie = str(payload.get("cookie") or "").strip() or config().p115.cookie
        if not cookie:
            return JSONResponse({"success": True, "data": {"ok": False, "error": "还没有 cookie"}})
        return JSONResponse({"success": True, "data": await test_cookie(cookie, config().p115.user_agent)})

    @app.get("/api/v1/reverse/cache/url_cache")
    async def url_cache(request: Request) -> JSONResponse:
        if not _authorized(config(), request):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        merged: dict[str, Any] = {}
        for port, ctx in sorted(runtime.ctxs.items()):
            for key, entry in ctx.state.redirects.snapshot().items():
                entry = dict(entry)
                entry["server"] = ctx.state.server.name
                entry["port"] = port
                merged[key] = entry
        return JSONResponse(
            {
                "success": True,
                "data": {"cache": merged, "cache_expire": config().server.redirect_cache_ttl},
            }
        )

    @app.post("/api/v1/reverse/cache/clear_url_cache")
    async def clear_one(request: Request) -> JSONResponse:
        if not _authorized(config(), request):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            payload = {}
        key = str(payload.get("key") or "")
        removed = any(ctx.state.redirects.drop(key) for ctx in runtime.ctxs.values())
        return JSONResponse({"success": removed, "message": "已清除" if removed else "未找到缓存"})

    @app.post("/api/v1/reverse/cache/clear_all_url_cache")
    async def clear_all(request: Request) -> JSONResponse:
        if not _authorized(config(), request):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        for ctx in runtime.ctxs.values():
            ctx.state.redirects.clear()
            ctx.state.playback.clear()
            ctx.state.resolver.clear()
        return JSONResponse({"success": True, "message": "已清空所有缓存"})

    return app


# -- entry point -----------------------------------------------------------


def _serve(app: FastAPI, host: str, port: int, log_level: str) -> uvicorn.Server:
    return uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, log_level=log_level.lower(), access_log=False)
    )


def run(config_path: str | os.PathLike[str]) -> None:
    config = load_config(config_path)
    logging.basicConfig(
        level=getattr(logging, str(config.server.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    runtime = Runtime(str(config_path), config)

    async def main() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:  # pragma: no cover - Windows
                pass
        serving = asyncio.create_task(runtime.start(), name="pley302:admin")
        await asyncio.wait(
            [serving, asyncio.create_task(stop.wait())], return_when=asyncio.FIRST_COMPLETED
        )
        await runtime.stop()
        if serving.done() and serving.exception():
            log.error("server failed: %s", serving.exception())

    asyncio.run(main())


def main() -> None:
    path = os.environ.get("PLEY302_CONFIG", "config.yaml")
    run(path)
