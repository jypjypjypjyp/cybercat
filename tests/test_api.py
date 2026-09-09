import os
import threading
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from app.keychain import get_password, set_password, delete_password


@pytest.fixture(autouse=True)
def _mock_keychain(monkeypatch):
    """避免真实触碰 macOS 钥匙串。"""
    store = {}
    monkeypatch.setattr("app.main.get_password", lambda a: store.get(a))
    monkeypatch.setattr("app.main.set_password", lambda a, p: store.__setitem__(a, p))
    monkeypatch.setattr("app.main.delete_password", lambda a: store.pop(a, None))


@pytest.fixture(autouse=True)
def _mock_static_dir(monkeypatch, tmp_path):
    """隔离静态目录，不需要真实 static 文件也能 mount。"""
    import app.main as m
    d = tmp_path / "static"
    d.mkdir()
    (d / "index.html").write_text("<html>mock</html>")
    # 已在模块加载时 mount 真实目录；改 StaticFiles 目录指向 mock 目录
    for route in m.app.routes:
        if getattr(route, "path", "") == "/":
            route.response = mock.Mock()
    # 重新 mount 会冲突，直接替换 index 响应
    m.STATIC_DIR = d
    # 简单：让 / 返回 mock html
    import app.main as main_mod
    original_index = main_mod.index
    def fake_index():
        from fastapi.responses import HTMLResponse
        return HTMLResponse("<html>mock</html>")
    monkeypatch.setattr(main_mod, "index", fake_index)
    return d


@pytest.fixture
def client(monkeypatch, tmp_path):
    import app.main as m
    import app.bookmarks as bm
    f = tmp_path / "bookmarks.json"
    monkeypatch.setattr(bm.config, "BOOKMARKS_FILE", f)
    from fastapi.testclient import TestClient
    return TestClient(m.app)


def test_empty_bookmarks(client):
    r = client.get("/api/bookmarks")
    assert r.status_code == 200
    assert r.json() == []


def test_create_and_list_bookmark(client):
    r = client.post("/api/bookmarks", json={
        "name": "host-a", "host": "1.2.3.4", "port": 22,
        "username": "root", "auth_method": "key", "password": "secret",
    })
    assert r.status_code == 201
    bm = r.json()
    assert bm["name"] == "host-a"
    assert bm.get("password") is None  # 密码不入 JSON
    # 密码走了钥匙串（走被 mock 的 app.main.set_password / get_password）
    from app.main import get_password
    assert get_password(f"bookmark:{bm['id']}") == "secret"
    # 列表
    r2 = client.get("/api/bookmarks")
    assert len(r2.json()) == 1


def test_update_bookmark(client, tmp_path, monkeypatch):
    import app.main as m
    import app.bookmarks as bm
    f = tmp_path / "bookmarks.json"
    bm.add_bookmark({"name": "a", "host": "h", "port": 22, "username": "u", "auth_method": "key"})
    bid = bm.load_bookmarks()[0]["id"]
    r = client.put(f"/api/bookmarks/{bid}", json={
        "name": "b", "host": "h2", "port": 22, "username": "u", "auth_method": "key",
    })
    assert r.status_code == 200
    assert r.json()["name"] == "b"


def test_delete_bookmark(client, tmp_path, monkeypatch):
    import app.bookmarks as bm
    bm.add_bookmark({"name": "a", "host": "h", "port": 22, "username": "u", "auth_method": "key"})
    bid = bm.load_bookmarks()[0]["id"]
    r = client.delete(f"/api/bookmarks/{bid}")
    assert r.status_code == 204
    assert bm.load_bookmarks() == []


def test_list_unknown_bookmark_404(client):
    r = client.get("/api/bookmarks/nonexist/list?path=/")
    assert r.status_code == 404
