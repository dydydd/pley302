from __future__ import annotations

import time

from pley302.cache import RedirectCache, TTLCache
from pley302.config import EmbyServerConfig, MappingRule
from pley302.mapping import PathMapper


def server(**overrides) -> EmbyServerConfig:
    base = {"name": "t", "host": "http://emby:8096", "proxy_port": 8098}
    base.update(overrides)
    return EmbyServerConfig.from_dict(base)


def test_longest_prefix_wins():
    mapper = PathMapper(
        server(
            filepath_mapping="""
            /CloudNAS/CloudDrive/115open/Media => /Media
            /CloudNAS/CloudDrive/115open/Media/4K => /Media-UHD
            """
        )
    )
    resolved = mapper.map("/CloudNAS/CloudDrive/115open/Media/4K/x.mkv")
    assert resolved.kind == "path"
    assert resolved.value == "/Media-UHD/x.mkv"


def test_unmapped_path_is_used_as_is():
    mapper = PathMapper(server())
    resolved = mapper.map("/Media/x.mkv")
    assert (resolved.kind, resolved.value) == ("path", "/Media/x.mkv")


def test_http_target_encodes_segments():
    mapper = PathMapper(server(filepath_mapping="/mnt/alist/115 => https://ol.example.com/d/115open"))
    resolved = mapper.map("/mnt/alist/115/电影/阿甘正传.mkv")
    assert resolved.kind == "http"
    assert resolved.value == "https://ol.example.com/d/115open/%E7%94%B5%E5%BD%B1/%E9%98%BF%E7%94%98%E6%AD%A3%E4%BC%A0.mkv"


def test_pickcode_target():
    mapper = PathMapper(server(filepath_mapping="/mnt/pc => pickcode:abcdef123"))
    resolved = mapper.map("/mnt/pc/sub/x.mkv")
    assert resolved.kind == "pickcode"
    assert resolved.value == "abcdef123"


def test_local_and_ignored_prefixes():
    mapper = PathMapper(server(local_filepath="/mnt/local", direct_path="/mnt/ignore"))
    assert mapper.is_local("/mnt/local/a.mkv")
    assert mapper.is_ignored("/mnt/ignore/b.mkv")
    assert not mapper.is_ignored("/mnt/ignored/b.mkv")
    assert mapper.map("/mnt/local/a.mkv").kind == "local"


def test_mapping_rule_requires_arrow():
    try:
        MappingRule.parse("/a /b")
    except ValueError as exc:
        assert "=>" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_redirect_cache_separates_user_agents():
    cache = RedirectCache.with_ttl(60)
    cache.put("/videos/1/stream", "PlayerA/1.0", "dev1", "https://cdn/1")
    assert cache.get("/videos/1/stream", "PlayerA/1.0", "dev1") == "https://cdn/1"
    assert cache.get("/videos/1/stream", "PlayerB/1.0", "dev1") is None
    assert cache.get("/videos/1/stream", "PlayerA/1.0", "dev2") is None


def test_redirect_cache_expires_and_has_the_listed_shape():
    cache = RedirectCache.with_ttl(1)
    cache.put("/videos/1/stream", "PlayerA/1.0", "dev1", "https://cdn/1")
    snapshot = cache.snapshot()
    assert len(snapshot) == 1
    entry = next(iter(snapshot.values()))
    assert entry["original_url"] == "/videos/1/stream"
    assert entry["redirect_url"] == "https://cdn/1"
    assert entry["user_agent"] == "PlayerA/1.0"
    assert entry["device_id"] == "dev1"
    assert isinstance(entry["timestamp"], int)
    time.sleep(1.1)
    assert cache.get("/videos/1/stream", "PlayerA/1.0", "dev1") is None
    assert cache.snapshot() == {}


def test_ttl_cache_evicts_oldest():
    cache: TTLCache[str] = TTLCache(60, max_entries=2)
    cache.set("a", "1")
    cache.set("b", "2")
    cache.set("c", "3")
    assert len(list(cache.items())) == 2


def test_mapping_onto_drive_root():
    mapper = PathMapper(server(filepath_mapping="/CloudNAS/CloudDrive/115open => /"))
    resolved = mapper.map("/CloudNAS/CloudDrive/115open/媒体库/片.mkv")
    assert resolved.value == "/媒体库/片.mkv"
    assert mapper.map("/CloudNAS/CloudDrive/115open").value == "/"
