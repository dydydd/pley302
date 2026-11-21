"""Emby side: ask the real server what a MediaSource's path is.

The client already holds a token (``api_key`` query parameter or
``X-Emby-Token`` header), so nothing here needs an administrator key: the
PlaybackInfo call is made with the caller's own credentials, which is why a
server entry has no token field.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import unquote

import httpx

log = logging.getLogger("pley302.emby")

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class EmbyError(RuntimeError):
    pass


def api_key_from(query: Mapping[str, str], headers: Mapping[str, str]) -> str:
    for name in ("api_key", "ApiKey", "apikey", "X-Emby-Token", "x-emby-token"):
        value = query.get(name)
        if value:
            return str(value)
    for name in ("x-emby-token", "X-Emby-Token", "x-mediabrowser-token"):
        value = headers.get(name) or headers.get(name.lower())
        if value:
            return str(value)
    token = headers.get("authorization") or headers.get("Authorization")
    if token:
        lowered = token.lower()
        if lowered.startswith("mediaBrowser "):
            return token.split(" ", 1)[1].strip()
        if lowered.startswith("bearer "):
            return token.split(" ", 1)[1].strip()
    return ""


def device_id_from(query: Mapping[str, str], headers: Mapping[str, str]) -> str:
    for name in ("DeviceId", "deviceId", "device_id"):
        value = query.get(name)
        if value:
            return str(value)
    for name in ("x-emby-device-id", "X-Emby-Device-Id", "x-emby-deviceid"):
        value = headers.get(name)
        if value:
            return str(value)
    return ""


@dataclass(slots=True)
class PlaybackAnswer:
    item_id: str
    info: dict[str, Any]
    media_source_id: str = ""

    def sources(self) -> list[dict[str, Any]]:
        sources = self.info.get("MediaSources")
        return sources if isinstance(sources, list) else []


class EmbyUpstream:
    """Thin async client for one Emby server."""

    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        json_body: Any = None,
        content: bytes | None = None,
    ) -> httpx.Response:
        url = f"{self.base_url}/{path.lstrip('/')}"
        if self._client is not None:
            return await self._client.request(
                method, url, params=params, headers=headers, json=json_body, content=content
            )
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            return await client.request(
                method, url, params=params, headers=headers, json=json_body, content=content
            )

    async def playback_info(
        self,
        item_id: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        body: Any = None,
        method: str = "POST",
    ) -> dict[str, Any]:
        path = f"Items/{item_id}/PlaybackInfo"
        response = await self._request(
            method,
            path,
            params=params,
            headers={k: v for k, v in (headers or {}).items() if k.lower() != "host"},
            json_body=body if method.upper() == "POST" else None,
        )
        if response.status_code >= 400:
            raise EmbyError(f"PlaybackInfo answered {response.status_code}: {response.text[:200]}")
        try:
            data = response.json()
        except Exception as exc:
            raise EmbyError(f"PlaybackInfo did not answer JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise EmbyError("PlaybackInfo answered something other than an object")
        return data


def normalize_media_source_id(raw: str) -> str:
    """Clients send the id raw, percent-encoded, or as a bare file name."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    return unquote(raw)


def pick_media_source(info: Mapping[str, Any], media_source_id: str) -> dict[str, Any] | None:
    sources = [s for s in (info.get("MediaSources") or []) if isinstance(s, dict)]
    if not sources:
        return None
    wanted = normalize_media_source_id(media_source_id)
    if not wanted:
        return sources[0]
    for source in sources:
        if str(source.get("Id") or "") == wanted:
            return source
    # Some clients pass the file name instead of the source id.
    for source in sources:
        path = str(source.get("Path") or "")
        name = path.replace("\\", "/").rsplit("/", 1)[-1]
        if name and (name == wanted or name == wanted.rsplit("/", 1)[-1]):
            return source
    for source in sources:
        if str(source.get("Name") or "") == wanted:
            return source
    # Some clients append a container: "<id>.mkv"
    for source in sources:
        source_id = str(source.get("Id") or "")
        if source_id and wanted.startswith(source_id + "."):
            return source
    return sources[0] if len(sources) == 1 else None


def media_source_path(source: Mapping[str, Any]) -> str:
    for key in ("Path", "path"):
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
