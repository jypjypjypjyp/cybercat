import app.bookmarks as bm


def test_add_and_list(tmp_path, monkeypatch):
    f = tmp_path / "bookmarks.json"
    monkeypatch.setattr(bm.config, "BOOKMARKS_FILE", f)
    bm.add_bookmark({"name": "host-a", "host": "1.2.3.4", "port": 22, "username": "u", "auth_method": "key"})
    lst = bm.load_bookmarks()
    assert len(lst) == 1
    assert lst[0]["name"] == "host-a"
    assert lst[0]["id"]
    assert lst[0]["timestamp"]


def test_update_bookmark(tmp_path, monkeypatch):
    f = tmp_path / "bookmarks.json"
    monkeypatch.setattr(bm.config, "BOOKMARKS_FILE", f)
    bm.add_bookmark({"name": "a", "host": "h", "port": 22, "username": "u", "auth_method": "key"})
    bid = bm.load_bookmarks()[0]["id"]
    bm.update_bookmark(bid, {"name": "b"})
    assert bm.get_bookmark(bid)["name"] == "b"


def test_delete_bookmark(tmp_path, monkeypatch):
    f = tmp_path / "bookmarks.json"
    monkeypatch.setattr(bm.config, "BOOKMARKS_FILE", f)
    bm.add_bookmark({"name": "a", "host": "h", "port": 22, "username": "u", "auth_method": "key"})
    bid = bm.load_bookmarks()[0]["id"]
    bm.delete_bookmark(bid)
    assert bm.load_bookmarks() == []


def test_add_and_list_defaults(tmp_path, monkeypatch):
    f = tmp_path / "bookmarks.json"
    monkeypatch.setattr(bm.config, "BOOKMARKS_FILE", f)
    bm.add_bookmark({"name": "x", "host": "h"})
    lst = bm.load_bookmarks()
    assert lst[0]["port"] == 22
    assert lst[0]["auth_method"] == "key"
    assert lst[0]["default_path"] == "/"
