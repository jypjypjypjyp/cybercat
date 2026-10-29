# 删除流式接口：NDJSON 事件序列、计数、单项失败不拖累其余项
import json
import os

import paramiko
from fastapi.testclient import TestClient

import app.main as main
import app.session_pool as sp
import app.sftp_ops as sfops
from tests.sftp_stub_server import run_sftp_server


def _connect(port):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname="127.0.0.1", port=port, username="user", password="pass",
                   timeout=5, allow_agent=False, look_for_keys=False)
    return client


def _events(resp):
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


def _session_bound_to_stub(monkeypatch):
    port, root, _ = run_sftp_server()
    session = sp.SFTPSession(_connect(port))
    monkeypatch.setattr(main, "_get_session", lambda bid: session)
    return session, root


def test_delete_stream_removes_tree_and_reports_counts(monkeypatch):
    session, root = _session_bound_to_stub(monkeypatch)
    os.makedirs(os.path.join(root, "tree", "sub"))
    with open(os.path.join(root, "tree", "a.txt"), "w") as f:
        f.write("hello")        # 5 字节
    with open(os.path.join(root, "tree", "sub", "b.txt"), "w") as f:
        f.write("xy")           # 2 字节

    with TestClient(main.app) as client:
        r = client.post("/api/bookmarks/x/delete", json={"paths": ["/tree"]})

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-ndjson")
    events = _events(r)
    assert events[0] == {"type": "start", "total": 1}
    done = events[-1]
    assert done["type"] == "done"
    assert (done["files"], done["dirs"], done["bytes"]) == (2, 2, 7)
    assert done["failed"] == []
    assert not os.path.exists(os.path.join(root, "tree"))
    session.close()


def test_delete_stream_missing_path_is_not_an_error(monkeypatch):
    """并发删/删到一半没了：结果与成功一致，前端不用弹红字。"""
    session, root = _session_bound_to_stub(monkeypatch)

    with TestClient(main.app) as client:
        r = client.post("/api/bookmarks/x/delete", json={"paths": ["/nope"]})

    events = _events(r)
    item = next(e for e in events if e["type"] == "item_done")
    assert item["ok"] is True
    assert events[-1]["failed"] == []
    session.close()


def test_delete_stream_continues_after_one_item_fails(monkeypatch):
    """一项失败只记一条，后面的照删——批量删除不该被第一个坏项卡死。"""
    session, root = _session_bound_to_stub(monkeypatch)
    with open(os.path.join(root, "keep.txt"), "w") as f:
        f.write("ok")

    original = sfops.delete_tree

    def fake_delete_tree(s, path, stop=None):
        if path == "/boom":
            raise OSError("权限不足")
        yield from original(s, path, stop)

    monkeypatch.setattr(sfops, "delete_tree", fake_delete_tree)

    with TestClient(main.app) as client:
        r = client.post("/api/bookmarks/x/delete", json={"paths": ["/boom", "/keep.txt"]})

    events = _events(r)
    results = {e["path"]: e for e in events if e["type"] == "item_done"}
    assert results["/boom"]["ok"] is False
    assert "权限不足" in results["/boom"]["error"]
    assert results["/keep.txt"]["ok"] is True
    done = events[-1]
    assert done["files"] == 1
    assert [f["path"] for f in done["failed"]] == ["/boom"]
    assert not os.path.exists(os.path.join(root, "keep.txt"))
    session.close()
