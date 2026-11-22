from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
import yaml

from pley302.app import Runtime, Saved, config_from_payload, create_admin_app
from pley302.config import AppConfig, load_config, mask_cookie, save_config, settings_view, to_yaml

COOKIE = "UID=123_A1_9; CID=abc; SEID=def; KID=ghi"


def base_config(**server) -> AppConfig:
    data = {
        "p115": {"cookie": COOKIE},
        "emby_servers": [
            {
                "name": "home",
                "host": "http://127.0.0.1:8096",
                "proxy_port": 8098,
                "filepath_mapping": "/mnt/cloud/Media => /Media",
                "library_roots": "/mnt/library",
            }
        ],
    }
    data["emby_servers"][0].update(server)
    return AppConfig.from_dict(data)


def test_settings_view_never_carries_the_cookie():
    view = settings_view(base_config())
    text = str(view)
    assert COOKIE not in text
    assert view["p115"]["cookie_set"] is True
    assert "123" in view["p115"]["cookie_hint"]
    assert mask_cookie("") == ""


def test_payload_keeps_the_cookie_when_the_field_is_blank():
    current = base_config()
    payload = {
        "p115": {"cookie": "", "user_agent": "UA/1", "api": "web"},
        "emby_servers": [
            {
                "name": "home",
                "host": "http://127.0.0.1:8096",
                "proxy_port": 8098,
                "filepath_mapping": "/mnt/cloud/Media => /Media\n/mnt/other => https://ol.example.com/d/115",
            }
        ],
    }
    config = config_from_payload(payload, current)
    assert config.p115.cookie == COOKIE
    assert config.p115.user_agent == "UA/1"
    assert [rule.kind for rule in config.emby_servers[0].filepath_mapping] == ["path", "http"]


def test_payload_replaces_the_cookie_when_given():
    config = config_from_payload(
        {"p115": {"cookie": "UID=9_Z; CID=1; SEID=2; KID=3"}}, base_config()
    )
    assert config.p115.cookie.startswith("UID=9_Z")


def test_payload_rejects_a_bad_server():
    with pytest.raises(ValueError, match="host"):
        config_from_payload(
            {"emby_servers": [{"name": "x", "host": "not-a-url", "proxy_port": 8098}]},
            base_config(),
        )
    with pytest.raises(ValueError, match="端口"):
        config_from_payload(
            {
                "emby_servers": [
                    {"name": "a", "host": "http://a", "proxy_port": 1},
                    {"name": "b", "host": "http://b", "proxy_port": 1},
                ]
            },
            base_config(),
        )


def test_config_round_trips_through_the_file(tmp_path: Path):
    config = base_config()
    path = tmp_path / "config.yaml"
    save_config(config, path)
    assert path.stat().st_mode & 0o777 == 0o600
    loaded = load_config(path)
    assert loaded.p115.cookie == COOKIE
    assert loaded.emby_servers[0].proxy_port == 8098
    assert loaded.emby_servers[0].filepath_mapping[0].source == "/mnt/cloud/Media"
    assert "emby_servers" in yaml.safe_load(to_yaml(config))


def call(app, method: str, url: str, token: str = "t0ken", **kwargs):
    async def run():
        transport = httpx.ASGITransport(app=app)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with httpx.AsyncClient(transport=transport, base_url="http://admin") as client:
            return await client.request(method, url, headers=headers, **kwargs)

    return asyncio.run(run())


def test_settings_api_saves_the_file_and_keeps_serving(tmp_path: Path):
    config = base_config(proxy_port=8098)
    path = tmp_path / "config.yaml"
    save_config(config, path)
    runtime = Runtime(str(path), config)
    applied: list[AppConfig] = []

    async def fake_sync():
        applied.append(runtime.config)
        return Saved(ports=[8098], removed=[], admin_port_changed=False)

    runtime._sync = fake_sync  # the listeners are not started in a unit test
    app = create_admin_app(runtime)

    listing = call(app, "GET", "/api/v1/settings")
    assert listing.status_code == 200
    assert listing.json()["data"]["emby_servers"][0]["name"] == "home"

    payload = {
        "server": {"url_reuse_seconds": 5, "prefetch": False},
        "p115": {"cookie": "UID=9_Z; CID=1; SEID=2; KID=3"},
        "emby_servers": [
            {
                "name": "renamed",
                "host": "http://127.0.0.1:8096",
                "proxy_port": 8099,
                "filepath_mapping": "/mnt/cloud/Media => /Media",
                "switch": True,
            }
        ],
    }
    saved = call(app, "POST", "/api/v1/settings", json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["ports"] == [8098]

    reloaded = load_config(path)
    assert reloaded.emby_servers[0].name == "renamed"
    assert reloaded.server.url_reuse_seconds == 5
    assert reloaded.server.prefetch is False
    assert reloaded.p115.cookie.startswith("UID=9_Z")
    assert applied, "the runtime was asked to apply the new configuration"
    assert "UID=9_Z" in str(applied[-1].p115.cookie)


def test_settings_api_needs_the_token(tmp_path: Path):
    config = base_config()
    config.server.admin_token = "t0ken"
    path = tmp_path / "config.yaml"
    save_config(config, path)
    runtime = Runtime(str(path), config)
    app = create_admin_app(runtime)
    assert call(app, "GET", "/api/v1/settings", token="").status_code == 401
    assert call(app, "GET", "/api/v1/settings", token="nope").status_code == 401
    assert call(app, "GET", "/api/v1/settings", token="t0ken").status_code == 200


def test_bad_payload_is_reported_not_saved(tmp_path: Path):
    config = base_config()
    path = tmp_path / "config.yaml"
    save_config(config, path)
    runtime = Runtime(str(path), config)
    app = create_admin_app(runtime)
    response = call(app, "POST", "/api/v1/settings", json={"emby_servers": []})
    assert response.status_code == 400
    assert "服务器" in response.json()["error"]
