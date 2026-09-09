import time
import uuid

import app.bookmarks as bookmarks
import app.sync as sync


def _write_config(tmp_path, text):
    p = tmp_path / "ssh_config"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_sync_creates_new(tmp_path, monkeypatch):
    f = tmp_path / "bookmarks.json"
    monkeypatch.setattr(bookmarks.config, "BOOKMARKS_FILE", f)
    cfg = _write_config(tmp_path, "Host host-a\n    HostName 192.0.2.10\n    Port 2222\n    User alice\n    IdentityFile ~/.ssh/id_rsa\n")
    r = sync.sync_from_ssh_config(cfg)
    assert r["created"] == 1 and r["updated"] == 0
    lst = bookmarks.load_bookmarks()
    assert len(lst) == 1
    bm = lst[0]
    assert bm["name"] == "host-a"
    assert bm["host"] == "192.0.2.10"
    assert bm["port"] == 2222
    assert bm["username"] == "alice"
    assert bm["auth_method"] == "key"
    assert bm["key_path"] == "~/.ssh/id_rsa"
    assert bm["id"]


def test_sync_updates_existing_keeps_id(tmp_path, monkeypatch):
    f = tmp_path / "bookmarks.json"
    monkeypatch.setattr(bookmarks.config, "BOOKMARKS_FILE", f)
    # 先手动建一个同名书签，端口故意设错
    bm = bookmarks.add_bookmark({"name": "host-a", "host": "0.0.0.0", "port": 22, "username": "x", "auth_method": "key"})
    original_id = bm["id"]
    cfg = _write_config(tmp_path, "Host host-a\n    HostName 192.0.2.10\n    Port 2222\n    User alice\n    IdentityFile ~/.ssh/id_rsa\n")
    r = sync.sync_from_ssh_config(cfg)
    assert r["created"] == 0 and r["updated"] == 1
    updated = bookmarks.get_bookmark(original_id)
    assert updated["id"] == original_id  # id 保留
    assert updated["host"] == "192.0.2.10"
    assert updated["port"] == 2222
    assert updated["username"] == "alice"


def test_sync_idempotent(tmp_path, monkeypatch):
    f = tmp_path / "bookmarks.json"
    monkeypatch.setattr(bookmarks.config, "BOOKMARKS_FILE", f)
    cfg = _write_config(tmp_path, "Host A\n    HostName 1.1.1.1\nHost B\n    HostName 2.2.2.2\n")
    sync.sync_from_ssh_config(cfg)
    r2 = sync.sync_from_ssh_config(cfg)
    # 第二次应全部覆盖，无新增
    assert r2["created"] == 0 and r2["updated"] == 2
    assert len(bookmarks.load_bookmarks()) == 2
