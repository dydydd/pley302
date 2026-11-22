"""Configuration model.

One entry per Emby-compatible server this service sits in front of: where it
is, which port to answer on, and how a path it reports turns into a path the
cloud drive knows.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def _lines(value: Any) -> list[str]:
    """Accept a YAML list or a block of newline separated lines."""
    if value is None:
        return []
    if isinstance(value, str):
        items = value.splitlines()
    else:
        items = list(value)
    return [str(item).strip() for item in items if str(item).strip()]


@dataclass(slots=True)
class MappingRule:
    """One ``a => b`` path replacement.

    ``kind`` is what ``b`` points at:

    * ``http``     - a URL (OpenList/AList ``/d/...`` link), 302 straight to it
    * ``pickcode`` - a 115 pickcode, resolved through the 115 API
    * ``path``     - a path in the 115 account, resolved through the 115 API
    * ``local``    - a path on this machine, served as bytes
    """

    source: str
    target: str
    kind: str

    @classmethod
    def parse(cls, line: str) -> "MappingRule":
        if "=>" not in line:
            raise ValueError(f"path mapping needs 'a => b', got {line!r}")
        source, target = (part.strip() for part in line.split("=>", 1))
        if not source or not target:
            raise ValueError(f"path mapping needs 'a => b', got {line!r}")
        source = source.rstrip("/") or "/"
        lowered = target.lower()
        if lowered.startswith(("http://", "https://")):
            kind = "http"
        elif lowered.startswith(("pickcode:", "115://")):
            kind = "pickcode"
        else:
            kind = "path"
        return cls(source=source, target=target, kind=kind)


@dataclass(slots=True)
class EmbyServerConfig:
    name: str = "emby"
    host: str = "http://127.0.0.1:8096"
    public_address: str = ""
    proxy_port: int = 8098
    filepath_mapping: list[MappingRule] = field(default_factory=list)
    local_filepath: list[str] = field(default_factory=list)
    direct_path: list[str] = field(default_factory=list)
    # Folders to read `.strm` files from, so a PlaybackInfo that names only the
    # file can still be turned into the media's path.
    library_roots: list[str] = field(default_factory=list)
    switch: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EmbyServerConfig":
        server = cls(
            name=str(data.get("name") or "emby"),
            host=str(data.get("host") or "").rstrip("/"),
            public_address=str(data.get("public_address") or "").rstrip("/"),
            proxy_port=int(data.get("proxy_port") or 8098),
            local_filepath=[p.rstrip("/") for p in _lines(data.get("local_filepath"))],
            direct_path=[p.rstrip("/") for p in _lines(data.get("direct_path"))],
            library_roots=[p.rstrip("/") for p in _lines(data.get("library_roots"))],
            switch=bool(data.get("switch", True)),
        )
        if not server.host:
            raise ValueError(f"emby server {server.name!r} has no host")
        server.filepath_mapping = [
            MappingRule.parse(line) for line in _lines(data.get("filepath_mapping"))
        ]
        return server


@dataclass(slots=True)
class ServerSettings:
    host: str = "0.0.0.0"
    admin_port: int = 15896
    admin_token: str = ""
    redirect_cache_ttl: int = 900
    # How long a 115 path -> file id answer is kept (the walk costs ~0.6 s).
    file_id_ttl: int = 3600
    # Where 115 path -> file id answers are kept between restarts (empty: memory only).
    file_id_cache_file: str = ""
    # How long one minted 115 URL may be handed out again, and to how many
    # callers at once (115 refuses the third concurrent open of a file).
    url_reuse_seconds: int = 60
    url_share_limit: int = 2
    mediasources_cache_ttl: int = 3600
    max_cache_entries: int = 4096
    rewrite_cors_js: bool = True
    # Send the 115 CDN URL with the client's own User-Agent, and keep the
    # request headers 115 attached to the redirect (some URLs require them).
    forward_client_ua: bool = True
    # Ask 115 for the first byte before handing a URL out: its edge sometimes
    # answers 502 for a URL it has just minted.
    verify_redirect: bool = False
    # When 115's edge won't open the URL it just minted, send the bytes through
    # this server instead of a 302, so playback still starts.
    fallback_proxy: bool = True
    # Resolve a media's URL while PlaybackInfo is being answered, so the play
    # request that follows is a cache hit.
    prefetch: bool = True
    log_level: str = "INFO"
    access_log: bool = False


@dataclass(slots=True)
class P115Settings:
    cookie: str = ""
    user_agent: str = DEFAULT_USER_AGENT
    api: str = "web"  # web | app
    timeout: float = 20.0


@dataclass(slots=True)
class AppConfig:
    server: ServerSettings = field(default_factory=ServerSettings)
    p115: P115Settings = field(default_factory=P115Settings)
    emby_servers: list[EmbyServerConfig] = field(default_factory=list)

    def enabled_servers(self) -> Iterable[EmbyServerConfig]:
        return (s for s in self.emby_servers if s.switch)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        server_data = dict(data.get("server") or {})
        settings = ServerSettings(
            host=str(server_data.get("host", "0.0.0.0")),
            admin_port=int(server_data.get("admin_port", 15896)),
            admin_token=str(server_data.get("admin_token") or ""),
            redirect_cache_ttl=int(server_data.get("redirect_cache_ttl", 900)),
            file_id_ttl=int(server_data.get("file_id_ttl", 3600)),
            file_id_cache_file=str(server_data.get("file_id_cache_file") or ""),
            url_reuse_seconds=int(server_data.get("url_reuse_seconds", 60)),
            url_share_limit=int(server_data.get("url_share_limit", 2)),
            mediasources_cache_ttl=int(server_data.get("mediasources_cache_ttl", 3600)),
            max_cache_entries=int(server_data.get("max_cache_entries", 4096)),
            rewrite_cors_js=bool(server_data.get("rewrite_cors_js", True)),
            forward_client_ua=bool(server_data.get("forward_client_ua", True)),
            verify_redirect=bool(server_data.get("verify_redirect", True)),
            fallback_proxy=bool(server_data.get("fallback_proxy", True)),
            prefetch=bool(server_data.get("prefetch", True)),
            log_level=str(server_data.get("log_level", "INFO")).upper(),
            access_log=bool(server_data.get("access_log", False)),
        )
        p115_data = dict(data.get("p115") or {})
        p115 = P115Settings(
            cookie=str(p115_data.get("cookie") or ""),
            user_agent=str(p115_data.get("user_agent") or DEFAULT_USER_AGENT),
            api=str(p115_data.get("api") or "web").lower(),
            timeout=float(p115_data.get("timeout", 20.0)),
        )
        servers = [EmbyServerConfig.from_dict(item) for item in data.get("emby_servers") or []]
        if not servers:
            raise ValueError("config has no emby_servers")
        ports = [s.proxy_port for s in servers if s.switch]
        if len(ports) != len(set(ports)):
            raise ValueError("two enabled emby servers share a proxy_port")
        return cls(server=settings, p115=p115, emby_servers=servers)


def load_config(path: str | os.PathLike[str]) -> AppConfig:
    raw = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(raw) or {}
    if not isinstance(data, dict):
        raise ValueError("config file must hold a mapping at the top level")
    return AppConfig.from_dict(data)


def mask_cookie(cookie: str) -> str:
    """Enough of the cookie to recognise it, never enough to use it."""
    if not cookie:
        return ""
    parts = [part.strip() for part in cookie.split(";") if part.strip()]
    uid = next((part for part in parts if part.upper().startswith("UID=")), "")
    if uid:
        value = uid.split("=", 1)[1]
        return f"UID={value.split('_')[0]}…  ({len(cookie)} 字符, {len(parts)} 段)"
    return f"已设置（{len(cookie)} 字符, {len(parts)} 段）"


def _rule_lines(rules: list[MappingRule]) -> str:
    return "\n".join(f"{rule.source} => {rule.target}" for rule in rules)


def _block(lines: list[str]) -> str:
    return "\n".join(lines)


def settings_view(config: "AppConfig") -> dict[str, Any]:
    """What the panel edits: everything but the secrets themselves."""
    server = config.server
    return {
        "server": {
            "admin_port": server.admin_port,
            "admin_token_set": bool(server.admin_token),
            "redirect_cache_ttl": server.redirect_cache_ttl,
            "file_id_ttl": server.file_id_ttl,
            "file_id_cache_file": server.file_id_cache_file,
            "url_reuse_seconds": server.url_reuse_seconds,
            "url_share_limit": server.url_share_limit,
            "mediasources_cache_ttl": server.mediasources_cache_ttl,
            "rewrite_cors_js": server.rewrite_cors_js,
            "verify_redirect": server.verify_redirect,
            "fallback_proxy": server.fallback_proxy,
            "prefetch": server.prefetch,
        },
        "p115": {
            "cookie_set": bool(config.p115.cookie),
            "cookie_hint": mask_cookie(config.p115.cookie),
            "user_agent": config.p115.user_agent,
            "api": config.p115.api,
        },
        "emby_servers": [
            {
                "name": item.name,
                "host": item.host,
                "public_address": item.public_address,
                "proxy_port": item.proxy_port,
                "switch": item.switch,
                "filepath_mapping": _rule_lines(item.filepath_mapping),
                "local_filepath": _block(item.local_filepath),
                "direct_path": _block(item.direct_path),
                "library_roots": _block(item.library_roots),
            }
            for item in config.emby_servers
        ],
    }


def to_yaml(config: "AppConfig") -> str:
    """The whole configuration as the file it was read from."""
    data: dict[str, Any] = {
        "server": {
            "host": config.server.host,
            "admin_port": config.server.admin_port,
            "admin_token": config.server.admin_token,
            "redirect_cache_ttl": config.server.redirect_cache_ttl,
            "file_id_ttl": config.server.file_id_ttl,
            "file_id_cache_file": config.server.file_id_cache_file,
            "url_reuse_seconds": config.server.url_reuse_seconds,
            "url_share_limit": config.server.url_share_limit,
            "mediasources_cache_ttl": config.server.mediasources_cache_ttl,
            "rewrite_cors_js": config.server.rewrite_cors_js,
            "forward_client_ua": config.server.forward_client_ua,
            "verify_redirect": config.server.verify_redirect,
            "fallback_proxy": config.server.fallback_proxy,
            "prefetch": config.server.prefetch,
            "log_level": config.server.log_level,
        },
        "p115": {
            "cookie": config.p115.cookie,
            "user_agent": config.p115.user_agent,
            "api": config.p115.api,
            "timeout": config.p115.timeout,
        },
        "emby_servers": [
            {
                "name": item.name,
                "host": item.host,
                "public_address": item.public_address,
                "proxy_port": item.proxy_port,
                "switch": item.switch,
                "filepath_mapping": _rule_lines(item.filepath_mapping),
                "local_filepath": _block(item.local_filepath),
                "direct_path": _block(item.direct_path),
                "library_roots": _block(item.library_roots),
            }
            for item in config.emby_servers
        ],
    }
    header = "# written by the pley302 admin panel\n"
    return header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)


def save_config(config: "AppConfig", path: str | os.PathLike[str]) -> None:
    """Write the file atomically, keeping it readable only by its owner."""
    import tempfile

    target = Path(path)
    text = to_yaml(config)
    handle = tempfile.NamedTemporaryFile(
        "w", dir=target.parent or Path("."), encoding="utf-8", delete=False
    )
    try:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.chmod(handle.name, 0o600)
    os.replace(handle.name, target)
