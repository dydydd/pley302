"""115 side: path (or pickcode) -> CDN URL.

The 115 download API encrypts its payload with RSA and decrypts the answer,
so this module drives ``p115client`` instead of re-implementing that handshake. The
pieces that matter for a 302:

* the URL is bound to the User-Agent that asked for it (``f=1`` in the query),
  so the lookup is done with the *player's* UA, and the cache is keyed by it;
* the URL expires (``t=...``), so it is cached only for the configured TTL.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .cache import TTLCache
from .config import P115Settings
from .mapping import ResolvedPath

log = logging.getLogger("pley302.p115")


class P115Error(RuntimeError):
    pass


class P115Unusable(P115Error):
    """A URL was handed out but would not open; it is kept for relaying."""

    def __init__(self, message: str, url: str) -> None:
        super().__init__(message)
        self.url = url


class P115Resolver:
    def __init__(
        self,
        settings: P115Settings,
        cache_ttl: int = 900,
        max_entries: int = 4096,
        verifier: Any = None,
        verify: bool = True,
        file_id_ttl: int = 3600,
        url_reuse_seconds: int = 60,
        url_share_limit: int = 2,
        file_id_cache_file: str = "",
    ) -> None:
        self.settings = settings
        self._cache: TTLCache[str] = TTLCache(cache_ttl, max_entries)
        # 115 path -> file id: the walk behind it costs ~0.6 s the first time
        # and the id never changes for a file at a path.
        self._ids: TTLCache[int] = TTLCache(file_id_ttl, max_entries * 4)
        # key -> {"url", "at", "handed"}: a minted URL costs ~0.2 s, and one
        # URL may go to a couple of callers at once before 115 refuses.
        self._urls: dict[Any, dict[str, Any]] = {}
        self.url_reuse_seconds = max(0, int(url_reuse_seconds))
        self.url_burst_seconds = 2.0
        self.url_share_limit = max(1, int(url_share_limit))
        self._client: Any = None
        self._client_error: str | None = None
        self._lock = asyncio.Lock()
        self._verify = verify
        self._verifier = verifier or self._probe
        self._http: Any = None
        self._pool: dict[Any, list[dict[str, Any]]] = {}
        self._sems: dict[str, asyncio.Semaphore] = {}
        self.per_url = 2
        # 115's edge refuses concurrent opens of the same file (its body reads
        # `{"message":"115 pmt 3-2","status":403}`); one at a time is what it
        # reliably serves, and one stream is all a player needs.
        self.per_file = 1
        self._id_file = file_id_cache_file
        self._id_ttl = max(60, int(file_id_ttl))
        self._id_dirty = False
        self._id_saved_at = 0.0
        self._load_ids()

    # -- setup ------------------------------------------------------------
    def _build_client(self) -> Any:
        if self._client is not None:
            return self._client
        if self._client_error is not None:
            raise P115Error(self._client_error)
        if not self.settings.cookie:
            self._client_error = "p115.cookie is empty, cannot resolve 115 paths"
            raise P115Error(self._client_error)
        try:
            from p115client import P115Client  # imported lazily so http-only setups run without it
        except Exception as exc:  # pragma: no cover - depends on the install
            self._client_error = f"p115client is not installed: {exc}"
            raise P115Error(self._client_error) from exc
        kwargs: dict[str, Any] = {}
        try:
            import inspect

            params = inspect.signature(P115Client.__init__).parameters
            if "check_for_relogin" in params:
                kwargs["check_for_relogin"] = False
        except Exception:  # pragma: no cover - introspection is best effort
            pass
        self._client = P115Client(self.settings.cookie, **kwargs)
        return self._client

    # -- public -----------------------------------------------------------
    async def resolve(self, target: ResolvedPath, user_agent: str, *, fresh: bool = False) -> str:
        """Return a URL to put in ``Location``.

        ``fresh`` skips the cache: a 115 URL allows only a couple of concurrent
        opens (``c=2``), so handing the same one to a player and to whatever
        else asks for the same file makes 115 answer 403/502.
        """
        if target.kind == "http":
            return target.value
        if target.kind == "local":
            raise P115Error("local paths are served by the proxy, not by 115")

        ua = user_agent if self.settings.api == "web" else self.settings.user_agent
        key = (target.kind, target.value, ua)
        now = time.time()
        if not fresh and self.url_reuse_seconds:
            entry = self._urls.get(key)
            if entry and (now - entry["at"]) < self.url_reuse_seconds:
                # Sequential use of one URL is fine (115 only refuses
                # *simultaneous* opens of a file), so reuse freely when the
                # last hand-off was a moment ago; a burst of requests within
                # one file gets a couple of shares and then its own URL.
                spaced = (now - entry.get("last", entry["at"])) > self.url_burst_seconds
                if spaced or entry["handed"] < self.url_share_limit:
                    entry["handed"] = 1 if spaced else entry["handed"] + 1
                    entry["last"] = now
                    log.debug("reused a URL for %s", target.value[-40:])
                    return entry["url"]

        started = time.perf_counter()
        url = await asyncio.to_thread(self._resolve_blocking, target, ua)
        log.info("minted %s in %.0f ms", target.value[-40:], (time.perf_counter() - started) * 1000)
        if self._verify:
            # 115's edge sometimes answers 502 for a URL it just handed out; a
            # player that gets that sees a broken file, so ask again once with
            # a fresh URL before answering.
            if not await self._verifier(url, ua):
                log.warning("115 URL for %s answered badly, resolving again", target.value)
                url = await asyncio.to_thread(self._resolve_blocking, target, ua)
                if not await self._verifier(url, ua):
                    raise P115Unusable(
                        f"115 handed out a URL for {target.value!r} that would not open", url
                    )
        self._urls[key] = {"url": url, "at": time.time(), "handed": 1, "last": time.time()}
        self._cache.set(key, url)
        return url

    # -- file ids on disk -------------------------------------------------
    def _load_ids(self) -> None:
        import json
        import os

        if not self._id_file or not os.path.exists(self._id_file):
            return
        try:
            data = json.loads(open(self._id_file, encoding="utf-8").read())
        except (OSError, ValueError):
            return
        now = time.time()
        for path, record in data.items():
            try:
                file_id, written = int(record["id"]), float(record["at"])
            except (TypeError, KeyError, ValueError):
                continue
            if now - written < self._id_ttl:
                self._ids.set(path, file_id)
        log.info("loaded %d cached file ids from %s", len(list(self._ids.items())), self._id_file)

    def _save_ids(self, force: bool = False) -> None:
        import json
        import os
        import tempfile

        if not self._id_file or not self._id_dirty:
            return
        now = time.time()
        if not force and now - self._id_saved_at < 30:
            return
        payload = {path: {"id": entry.value, "at": int(entry.created_at)} for path, entry in self._ids.items()}
        try:
            directory = os.path.dirname(self._id_file) or "."
            os.makedirs(directory, exist_ok=True)
            handle = tempfile.NamedTemporaryFile("w", dir=directory, delete=False, encoding="utf-8")
            json.dump(payload, handle)
            handle.close()
            os.replace(handle.name, self._id_file)
            self._id_dirty = False
            self._id_saved_at = now
        except OSError as exc:  # pragma: no cover - a read-only folder is fine
            log.warning("could not save file ids to %s: %s", self._id_file, exc)

    async def _probe(self, url: str, user_agent: str) -> bool:
        """Ask for the first byte, the way a player's first request will."""
        import httpx

        if self._http is None:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=5.0), follow_redirects=True)
        headers = {"Range": "bytes=0-0"}
        if user_agent:
            headers["User-Agent"] = user_agent
        try:
            response = await self._http.get(url, headers=headers)
        except httpx.HTTPError as exc:
            log.warning("115 URL probe failed: %s", exc)
            return False
        ok = response.status_code in (200, 206, 416)
        if not ok:
            log.warning("115 URL probe answered %s", response.status_code)
        return ok

    def _resolve_blocking(self, target: ResolvedPath, user_agent: str) -> str:
        client = self._build_client()
        try:
            if target.kind == "pickcode":
                return self._download_url(client, target.value, user_agent)
            file_id = self._file_id(client, target.value)
            return self._download_url(client, file_id, user_agent)
        except P115Error:
            raise
        except Exception as exc:
            raise P115Error(f"115 lookup failed for {target.value!r}: {exc}") from exc

    # -- internals --------------------------------------------------------
    def _file_id(self, client: Any, path: str) -> int:
        known = self._ids.get(path)
        if known is not None:
            return known
        file_id = self._file_id_uncached(client, path)
        self._ids.set(path, file_id)
        self._id_dirty = True
        self._save_ids()
        return file_id

    def _file_id_uncached(self, client: Any, path: str) -> int:
        fs = getattr(client, "fs", None)
        for owner in (fs, client):
            get_id = getattr(owner, "get_id", None)
            if get_id is None:
                continue
            # A long-running process keeps p115client's own directory listings;
            # a file added since the listing was read looks missing until it is
            # asked for again with `refresh=True`.
            for kwargs in ({}, {"refresh": True}):
                try:
                    file_id = get_id(path, **kwargs)
                except TypeError:
                    if kwargs:
                        raise
                    break
                except Exception as exc:
                    if kwargs:
                        raise P115Error(f"115 has no file at {path!r}: {exc}") from exc
                    log.debug("115 has %r cached as missing, asking again", path)
                    continue
                if file_id is None:
                    continue
                return int(file_id)
            raise P115Error(f"115 has no file at {path!r}")
        raise P115Error("p115client exposes no get_id(); cannot map a path to a file id")

    def _download_url(self, client: Any, pickcode_or_id: Any, user_agent: str) -> str:
        attempts: list[dict[str, Any]] = [
            {"pickcode": pickcode_or_id, "user_agent": user_agent},
            {"pickcode": pickcode_or_id},
        ]
        last: Exception | None = None
        for kwargs in attempts:
            try:
                result = client.download_url(kwargs.pop("pickcode"), **kwargs)
            except TypeError as exc:  # older signature without user_agent=
                last = exc
                continue
            except Exception as exc:
                last = exc
                continue
            url = getattr(result, "url", None) or str(result)
            if url:
                return url
            last = P115Error("115 returned an empty download URL")
        raise P115Error(f"115 refused to hand out a download URL: {last}")

    def clear(self) -> None:
        self._cache.clear()
        self._urls.clear()
        self._ids.clear()

    def warm(self, target: ResolvedPath, user_agent: str) -> None:
        """Resolve a path now, so the request that follows finds it ready."""
        try:
            asyncio.get_running_loop().create_task(self.resolve(target, user_agent))
        except RuntimeError:  # pragma: no cover - no loop, nothing to warm
            pass

    def slot(self, file_key: str):
        """A gate of ``per_file`` concurrent opens for one file."""
        sem = self._sems.get(file_key)
        if sem is None:
            sem = asyncio.Semaphore(self.per_file)
            self._sems[file_key] = sem
        return sem

    async def lease(self, target: ResolvedPath, user_agent: str) -> str:
        """A URL for one open, minted when the ones in hand are busy.

        115 allows ``c`` concurrent opens per URL; relaying many ranges of one
        file at once reuses them past that, and 115 starts refusing.
        """
        if target.kind in ("http", "local"):
            return await self.resolve(target, user_agent)
        ua = user_agent if self.settings.api == "web" else self.settings.user_agent
        key = (target.kind, target.value, ua)
        async with self._lock:
            pool = self._pool.setdefault(key, [])
            for entry in pool:
                if entry["inflight"] < self.per_url and (time.time() - entry["at"]) < self.settings.timeout * 30:
                    entry["inflight"] += 1
                    return entry["url"]
        url = await self.resolve(target, user_agent, fresh=True)
        async with self._lock:
            self._pool.setdefault(key, []).append({"url": url, "inflight": 1, "at": time.time()})
        return url

    async def release(self, url: str) -> None:
        async with self._lock:
            for pool in self._pool.values():
                for entry in pool:
                    if entry["url"] == url and entry["inflight"] > 0:
                        entry["inflight"] -= 1
                        return

    async def aclose(self) -> None:
        if self._http is not None:
            try:
                await self._http.aclose()
            except Exception:  # pragma: no cover
                pass
            self._http = None
        self.close()

    def close(self) -> None:
        self._save_ids(force=True)
        client = self._client
        self._client = None
        if client is None:
            return
        for name in ("close", "session", "_session"):
            close = getattr(client, name, None)
            if close is None:
                continue
            try:
                close()
            except Exception:  # pragma: no cover - cleanup is best effort
                pass
            return
