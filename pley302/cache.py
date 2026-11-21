"""Caches.

There are two: the resolved redirect per (original URL, User-Agent, device)
and the PlaybackInfo answer already inspected. The first one is
what its cache page lists, and the three-field key is not decoration: 115 ties a
download URL to the User-Agent that asked for it, so two players on the same
file must not share one entry.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Generic, Iterable, TypeVar

T = TypeVar("T")

KEY = tuple[str, str, str]


@dataclass(slots=True)
class CacheEntry(Generic[T]):
    value: T
    created_at: float
    ttl: float
    # Only redirect entries carry these; kept here so the admin view can list
    # which player was handed what.
    original_url: str = ""
    redirect_url: str = ""
    user_agent: str = ""
    device_id: str = ""

    def expires_at(self) -> float:
        return self.created_at + self.ttl

    def alive(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return self.ttl <= 0 or now < self.expires_at()

    def remaining(self, now: float | None = None) -> int:
        if self.ttl <= 0:
            return -1
        now = time.time() if now is None else now
        return max(0, int(self.expires_at() - now))


class TTLCache(Generic[T]):
    """Small thread-safe TTL cache; the service is asyncio but 115 lookups run
    in a worker thread, so a lock is the simple correct thing."""

    def __init__(self, ttl: float, max_entries: int = 4096) -> None:
        self.ttl = float(ttl)
        self.max_entries = max(1, int(max_entries))
        self._items: dict[Any, CacheEntry[T]] = {}
        self._lock = threading.Lock()

    def get(self, key: Any) -> T | None:
        now = time.time()
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return None
            if not entry.alive(now):
                self._items.pop(key, None)
                return None
            return entry.value

    def entry(self, key: Any) -> CacheEntry[T] | None:
        now = time.time()
        with self._lock:
            entry = self._items.get(key)
            if entry is None or not entry.alive(now):
                self._items.pop(key, None)
                return None
            return entry

    def set(self, key: Any, value: T, **meta: str) -> CacheEntry[T]:
        now = time.time()
        entry: CacheEntry[T] = CacheEntry(
            value=value, created_at=now, ttl=self.ttl, **meta
        )
        with self._lock:
            self._items[key] = entry
            if len(self._items) > self.max_entries:
                self._evict_locked(now)
        return entry

    def pop(self, key: Any) -> None:
        with self._lock:
            self._items.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def purge_expired(self) -> int:
        now = time.time()
        with self._lock:
            dead = [k for k, e in self._items.items() if not e.alive(now)]
            for key in dead:
                self._items.pop(key, None)
            return len(dead)

    def items(self) -> Iterable[tuple[Any, CacheEntry[T]]]:
        now = time.time()
        with self._lock:
            return [(k, e) for k, e in self._items.items() if e.alive(now)]

    def _evict_locked(self, now: float) -> None:
        for key in [k for k, e in self._items.items() if not e.alive(now)]:
            self._items.pop(key, None)
        while len(self._items) > self.max_entries:
            oldest = min(self._items, key=lambda k: self._items[k].created_at)
            self._items.pop(oldest, None)


@dataclass(slots=True)
class RedirectCache:
    """original_url + User-Agent + device id -> CDN URL."""

    cache: TTLCache[str] = field(default_factory=lambda: TTLCache(900))

    @classmethod
    def with_ttl(cls, ttl: int, max_entries: int = 4096) -> "RedirectCache":
        return cls(cache=TTLCache(ttl, max_entries))

    @staticmethod
    def make_key(original_url: str, user_agent: str, device_id: str) -> KEY:
        return (original_url, user_agent or "", device_id or "")

    def get(self, original_url: str, user_agent: str, device_id: str) -> str | None:
        return self.cache.get(self.make_key(original_url, user_agent, device_id))

    def put(self, original_url: str, user_agent: str, device_id: str, redirect_url: str) -> None:
        self.cache.set(
            self.make_key(original_url, user_agent, device_id),
            redirect_url,
            original_url=original_url,
            redirect_url=redirect_url,
            user_agent=user_agent or "",
            device_id=device_id or "",
        )

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Shape used by /reverse/cache/url_cache: a dict keyed by a printable
        key, holding the timestamp, the two URLs, the User-Agent and the device."""
        out: dict[str, dict[str, Any]] = {}
        for key, entry in self.cache.items():
            original_url, user_agent, device_id = key
            out[f"{device_id}:{original_url}:{user_agent}"] = {
                "original_url": original_url,
                "redirect_url": entry.value,
                "timestamp": int(entry.created_at),
                "user_agent": user_agent,
                "device_id": device_id,
            }
        return out

    def drop(self, printable_key: str) -> bool:
        for key, _entry in list(self.cache.items()):
            original_url, user_agent, device_id = key
            if printable_key in (
                f"{device_id}:{original_url}:{user_agent}",
                original_url,
                f"{device_id}{original_url}{user_agent}",
            ):
                self.cache.pop(key)
                return True
        return False

    def clear(self) -> None:
        self.cache.clear()

    @property
    def ttl(self) -> float:
        return self.cache.ttl
