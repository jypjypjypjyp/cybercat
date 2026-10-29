"""设置：字段映射、校验、写盘生效、接口回归。"""
import json

import pytest
from fastapi.testclient import TestClient

import app.config as config
import app.main as m
import app.settings as settings


def test_fields_all_map_to_config():
    for field in settings.FIELDS:
        assert field["key"] in settings._ATTR
        assert hasattr(config, settings._ATTR[field["key"]])


def test_update_applies_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "FILE", tmp_path / "settings.json")
    old = (config.PREVIEW_LANES, config.PREVIEW_MAX_BYTES, config.IDLE_TIMEOUT)
    try:
        settings.update({"preview_lanes": 2, "preview_max_mb": 32, "idle_timeout_min": 5})
        assert config.PREVIEW_LANES == 2
        assert config.PREVIEW_MAX_BYTES == 32 * 1024 * 1024
        assert config.IDLE_TIMEOUT == 300
        saved = json.loads((tmp_path / "settings.json").read_text())
        assert saved["preview_lanes"] == 2 and saved["preview_max_mb"] == 32
        assert settings.values()["preview_max_mb"] == 32
        assert settings.values()["idle_timeout_min"] == 5
    finally:
        config.PREVIEW_LANES, config.PREVIEW_MAX_BYTES, config.IDLE_TIMEOUT = old


@pytest.mark.parametrize("patch", [
    {"preview_lanes": 0},
    {"preview_lanes": 99},
    {"preview_lanes": "abc"},
    {"transcode_preset": "slowest"},
    {"preview_max_mb": 8},
])
def test_invalid_leaves_everything_untouched(tmp_path, monkeypatch, patch):
    monkeypatch.setattr(settings, "FILE", tmp_path / "settings.json")
    before = settings.values()
    with pytest.raises(ValueError):
        settings.update(patch)
    assert settings.values() == before
    assert not (tmp_path / "settings.json").exists()


def test_load_ignores_broken_file(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text("{not json")
    monkeypatch.setattr(settings, "FILE", path)
    before = settings.values()
    settings.load()
    assert settings.values() == before


def test_api_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "FILE", tmp_path / "settings.json")
    old = config.TRANSCODE_CRF
    try:
        client = TestClient(m.app)
        got = client.get("/api/settings")
        assert got.status_code == 200
        assert {f["key"] for f in got.json()["fields"]} == set(settings._ATTR)
        put = client.put("/api/settings", json={"transcode_crf": 30})
        assert put.status_code == 200 and put.json()["values"]["transcode_crf"] == 30
        assert config.TRANSCODE_CRF == 30
        bad = client.put("/api/settings", json={"preview_lanes": 0})
        assert bad.status_code == 400
        assert config.TRANSCODE_CRF == 30
    finally:
        config.TRANSCODE_CRF = old
