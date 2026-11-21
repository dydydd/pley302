"""The `.strm` table.

Some servers hand a player only the file *name* of a strm item
(`/Example Movie.strm`), not the folder, so a proxy in front of them cannot
derive the media's path from PlaybackInfo alone. What it can do is keep a table
of its own: every `.strm` under the library folders, by name, with the address
(or 115 path) its first line holds.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

MAX_STRM_BYTES = 64 * 1024


def first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return ""


class StrmIndex:
    """Name -> the address a `.strm` file names, refreshed on a TTL."""

    def __init__(self, roots: list[str], ttl: float = 120.0) -> None:
        self.roots = [root.rstrip("/") or "/" for root in roots]
        self.ttl = ttl
        self._map: dict[str, str] = {}
        self._built_at = 0.0
        self._lock = threading.Lock()

    def _fresh(self) -> bool:
        return bool(self._map) and (time.time() - self._built_at) < self.ttl

    def refresh(self) -> int:
        found: dict[str, str] = {}
        for root in self.roots:
            if not os.path.isdir(root):
                continue
            for dirpath, _dirnames, filenames in os.walk(root):
                for name in filenames:
                    if not name.lower().endswith(".strm"):
                        continue
                    full = Path(dirpath) / name
                    found.setdefault(name, str(full))
                    found.setdefault(name[: -len(".strm")], str(full))
        with self._lock:
            self._map = found
            self._built_at = time.time()
        return len(found)

    def locate(self, name: str) -> str | None:
        if not self.roots:
            return None
        if not self._fresh():
            self.refresh()
        base = os.path.basename(name)
        with self._lock:
            return self._map.get(base) or self._map.get(base[: -len(".strm")] if base.endswith(".strm") else base)

    def target(self, name: str) -> str | None:
        """The first meaningful line of the `.strm` file ``name`` points at."""
        path = self.locate(name)
        if path is None:
            return None
        try:
            with open(path, "rb") as handle:
                raw = handle.read(MAX_STRM_BYTES)
        except OSError:
            return None
        for encoding in ("utf-8-sig", "utf-16"):
            try:
                text = raw.decode(encoding)
            except (UnicodeDecodeError, UnicodeError):
                continue
            line = first_line(text)
            if line:
                return line
        return None
