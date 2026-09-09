import app.bookmarks as bookmarks
import app.main as m
from fastapi.testclient import TestClient


def test_sync_endpoint_creates_bookmarks(tmp_path, monkeypatch):
    import app.sync as sync_mod
    f = tmp_path / "bookmarks.json"
    monkeypatch.setattr(bookmarks.config, "BOOKMARKS_FILE", f)
    cfg = tmp_path / "ssh_config"
    cfg.write_text("Host host-a\n    HostName 192.0.2.10\n    Port 2222\n    User alice\n    IdentityFile ~/.ssh/id_rsa\n",
                   encoding="utf-8")
    # 让真实 sync 读这个假 config
    monkeypatch.setattr(sync_mod.sshcfg, "load_ssh_config", lambda path=None: sync_mod.sshcfg.parse_ssh_config(cfg.read_text()))
    client = TestClient(m.app)
    r = client.post("/api/sync-ssh-config")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["created"] == 1
    lst = bookmarks.load_bookmarks()
    assert lst[0]["name"] == "host-a"
    assert lst[0]["port"] == 2222


def test_sync_endpoint_error(tmp_path, monkeypatch):
    f = tmp_path / "bookmarks.json"
    monkeypatch.setattr(bookmarks.config, "BOOKMARKS_FILE", f)
    monkeypatch.setattr("app.main.syncer.sync_from_ssh_config", lambda path=None: (_ for _ in ()).throw(OSError("boom")))
    client = TestClient(m.app)
    r = client.post("/api/sync-ssh-config")
    assert r.status_code == 500
