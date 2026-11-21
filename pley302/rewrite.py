"""Rewrite a PlaybackInfo answer so the player asks *us* for the bytes.

Emby's answer already names the direct-play URL of every MediaSource; the only
thing that has to change is where that URL points. The player then requests it,
gets a 302, and pulls the media from the CDN itself. Fields rewritten are the
ones Emby clients actually read: ``Path``, ``DirectStreamUrl``, ``DirectPlayUrl``
(and ``TranscodingUrl`` when asked), plus the flags that make a client treat the
source as remotely reachable over plain HTTP.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping
from urllib.parse import quote, urlencode


def redirect_url_for(
    base_url: str,
    item_id: str,
    media_source_id: str,
    *,
    api_key: str = "",
    device_id: str = "",
    static: bool = True,
    extra: Mapping[str, str] | None = None,
) -> str:
    query = {"MediaSourceId": media_source_id or ""}
    if static:
        query["Static"] = "true"
    if device_id:
        query["DeviceId"] = device_id
    if api_key:
        query["api_key"] = api_key
    if extra:
        for key, value in extra.items():
            if key.lower() in {
                "mediasourceid",
                "static",
                "deviceid",
                "api_key",
                "api_key",
                "starttimeticks",
                "playbackapikey",
            }:
                continue
            query[key] = value
    return f"{base_url.rstrip('/')}/videos/{quote(item_id, safe='')}/stream?{urlencode(query)}"


def rewrite_playback_info(
    info: Mapping[str, Any],
    *,
    item_id: str,
    base_url: str,
    api_key: str = "",
    device_id: str = "",
    rewrite_transcoding: bool = False,
    extra: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return a copy of ``info`` whose MediaSources all stream through us."""
    result = deepcopy(dict(info))
    sources = result.get("MediaSources")
    if not isinstance(sources, list):
        return result

    for source in sources:
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("Id") or "")
        target = redirect_url_for(
            base_url,
            item_id,
            source_id,
            api_key=api_key,
            device_id=device_id,
            extra=extra,
        )
        source["Path"] = target
        source["DirectStreamUrl"] = target
        source["DirectPlayUrl"] = target
        if rewrite_transcoding:
            source["TranscodingUrl"] = target
        source["Protocol"] = "Http"
        source["IsRemote"] = True
        source["SupportsDirectPlay"] = True
        source["SupportsDirectStream"] = True
        source["RequiresOpening"] = False
        source["RequiresClosing"] = False
        # A remote source cannot be probed for bitrate locally; Emby clients
        # fall back to whatever the container advertises, which is what we want.
        if source.get("IsInfiniteStream") is None:
            source["IsInfiniteStream"] = False

    return result
