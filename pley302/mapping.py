"""Path mapping: turn the path Emby knows into something 115 (or OpenList) knows.

Emby reports the path it sees in its library, which is usually a mount from
CloudDrive/OpenList (``/CloudNAS/CloudDrive/115open/Media/...``). 115 only
answers for its own view of the same file (``/Media/...``), so the mapping a
user writes is applied longest-prefix first, one `a => b` per line.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from .config import EmbyServerConfig, MappingRule


@dataclass(slots=True)
class ResolvedPath:
    """What a mapped path should turn into."""

    kind: str  # http | pickcode | path | local
    value: str
    source: str  # the original Emby path, kept for diagnostics and caching

    @property
    def needs_115(self) -> bool:
        return self.kind in ("pickcode", "path")


def _norm(path: str) -> str:
    path = path.replace("\\", "/")
    if not path.startswith("/"):
        path = "/" + path
    while "//" in path:
        path = path.replace("//", "/")
    return path


def _join(target: str, remainder: str) -> str:
    if not remainder:
        return target
    base = target.rstrip("/")
    return base + "/" + remainder.lstrip("/")


def _join_url(target: str, remainder: str) -> str:
    target = target.rstrip("/")
    if not remainder:
        return target + "/"
    encoded = "/".join(quote(part, safe="") for part in remainder.strip("/").split("/"))
    return target + "/" + encoded


class PathMapper:
    def __init__(self, config: EmbyServerConfig) -> None:
        self.direct_prefixes = [_norm(p) for p in config.direct_path]
        self.local_prefixes = [_norm(p) for p in config.local_filepath]
        self.rules = sorted(config.filepath_mapping, key=lambda r: len(r.source), reverse=True)
        self.local_rules = [
            MappingRule(source=prefix, target=prefix, kind="local")
            for prefix in self.local_prefixes
            if not any(r.source == prefix for r in self.rules)
        ]

    # -- classification ---------------------------------------------------
    def is_ignored(self, path: str) -> bool:
        """Paths listed in "忽略路径" are passed to Emby untouched."""
        path = _norm(path)
        return any(path == p or path.startswith(p + "/") for p in self.direct_prefixes)

    def is_local(self, path: str) -> bool:
        path = _norm(path)
        return any(path == p or path.startswith(p + "/") for p in self.local_prefixes)

    # -- mapping ----------------------------------------------------------
    def map(self, path: str) -> ResolvedPath:
        path = _norm(path)
        for rule in list(self.rules) + self.local_rules:
            source = rule.source
            if path == source:
                remainder = ""
            elif path.startswith(source.rstrip("/") + "/"):
                remainder = path[len(source.rstrip("/")) :]
            else:
                continue
            if rule.kind == "http":
                return ResolvedPath(kind="http", value=_join_url(rule.target, remainder), source=path)
            if rule.kind == "pickcode":
                pickcode = rule.target
                for prefix in ("pickcode://", "pickcode:", "115://"):
                    if pickcode.lower().startswith(prefix):
                        pickcode = pickcode[len(prefix) :]
                        break
                return ResolvedPath(kind="pickcode", value=pickcode.strip("/"), source=path)
            if rule.kind == "local":
                return ResolvedPath(kind="local", value=_join(rule.target, remainder), source=path)
            return ResolvedPath(kind="path", value=_join(rule.target, remainder), source=path)
        # No rule matched: the path is already what 115 would show.
        return ResolvedPath(kind="path", value=path, source=path)
