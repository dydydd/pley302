"""Wiring: one proxy per configured Emby server, plus a small admin API.

The admin API lists the redirects this service handed out, per server, with
the same fields a cache viewer needs: the original request, the URL it went
to, the User-Agent and device it was minted for.
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
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .admin_ui import INDEX_HTML
from .cache import RedirectCache, TTLCache
from .config import AppConfig, load_config
from .emby import EmbyUpstream
from .index import StrmIndex
from .mapping import PathMapper
from .p115 import P115Resolver
from .proxy import ProxyState, create_app

log = logging.getLogger("pley302")

APP_VERSION = "1.0.0"


def build_state(config: AppConfig) -> dict[int, ProxyState]:
    states: dict[int, ProxyState] = {}
    for server in config.enabled_servers():
        states[server.proxy_port] = ProxyState(
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
                timeout=httpx.Timeout(60.0, connect=10.0),
                follow_redirects=False,
            ),
            index=StrmIndex(server.library_roots),
        )
    for state in states.values():
        # Walk the library folders once, at startup, not on a player's request.
        try:
            state.index.refresh()
        except Exception as exc:  # pragma: no cover - a missing folder is fine
            log.warning("could not index %s: %s", state.server.library_roots, exc)
    return states


def _authorized(config: AppConfig, request: Request) -> bool:
    token = config.server.admin_token
    if not token:
        return True
    header = request.headers.get("authorization") or ""
    return header.removeprefix("Bearer ").strip() == token


def create_admin_app(config: AppConfig, states: dict[int, ProxyState]) -> FastAPI:
    app = FastAPI(title="pley302 admin", docs_url=None, redoc_url=None, openapi_url=None)

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
                        "name": state.server.name,
                        "host": state.server.host,
                        "proxy_port": state.server.proxy_port,
                        "redirects": len(state.redirects.snapshot()),
                    }
                    for state in states.values()
                ],
            },
        }

    @app.get("/api/v1/reverse/cache/url_cache")
    async def url_cache(request: Request) -> JSONResponse:
        if not _authorized(config, request):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        merged: dict[str, Any] = {}
        expiry = config.server.redirect_cache_ttl
        for state in states.values():
            for key, entry in state.redirects.snapshot().items():
                entry = dict(entry)
                entry["server"] = state.server.name
                merged[key] = entry
        return JSONResponse({"success": True, "data": {"cache": merged, "cache_expire": expiry}})

    @app.post("/api/v1/reverse/cache/clear_url_cache")
    async def clear_one(request: Request) -> JSONResponse:
        if not _authorized(config, request):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        payload = {}
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        key = str(payload.get("key") or "")
        removed = any(state.redirects.drop(key) for state in states.values())
        return JSONResponse({"success": removed, "message": "已清除" if removed else "未找到缓存"})

    @app.post("/api/v1/reverse/cache/clear_all_url_cache")
    async def clear_all(request: Request) -> JSONResponse:
        if not _authorized(config, request):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        for state in states.values():
            state.redirects.clear()
            state.playback.clear()
            state.resolver.clear()
        return JSONResponse({"success": True, "message": "已清空所有缓存"})

    return app


async def _serve(app: FastAPI, host: str, port: int, log_level: str) -> None:
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level=log_level.lower(),
            access_log=False,
        )
    )
    await server.serve()


def run(config_path: str | os.PathLike[str]) -> None:
    config = load_config(config_path)
    logging.basicConfig(
        level=getattr(logging, config.server.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    states = build_state(config)
    apps: list[tuple[FastAPI, int, str]] = []
    for port, state in states.items():
        apps.append((create_app(config, state.server, state), port, f"proxy:{state.server.name}"))
    apps.append((create_admin_app(config, states), config.server.admin_port, "admin"))

    async def main() -> None:
        tasks = [
            asyncio.create_task(
                _serve(app, config.server.host, port, config.server.log_level),
                name=f"serve:{label}",
            )
            for app, port, label in apps
        ]
        for app, port, label in apps:
            log.info("%s listening on %s:%s", label, config.server.host, port)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:  # Windows
                pass
        done, pending = await asyncio.wait(
            [*tasks, asyncio.create_task(stop.wait())], return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in done:
            if task.exception():
                log.error("server task failed: %s", task.exception())

    asyncio.run(main())


def main() -> None:
    path = os.environ.get("PLEY302_CONFIG", "config.yaml")
    run(path)
