"""预览：Content-Type / inline / Range 语义 / 目录与不存在文件 / 预览通道不占传输闸门。"""
import asyncio
import os

import pytest
from fastapi.testclient import TestClient

import app.main as m
import app.config as config
import app.preview as pv
import app.session_pool as sp
from tests.sftp_stub_server import run_sftp_server

PAYLOAD = bytes(range(256)) * 4  # 1024 字节，内容可校验


# ---------- 纯函数：扩展名 → MIME ----------

@pytest.mark.parametrize("path,expect", [
    ("/a/b/pic.PNG", "image/png"),
    ("/a/v.mp4", "video/mp4"),
    ("/a/v.webm", "video/webm"),
    ("/a/s.mp3", "audio/mpeg"),
    ("/a/s.flac", "audio/flac"),
    ("/a/doc.pdf", "application/pdf"),
    ("/a/data.json", "application/json"),
    ("/a/x.py", pv.TEXT_MIME),
    ("/a/README.md", pv.TEXT_MIME),
    ("/a/Makefile", pv.TEXT_MIME),
    ("/a/.gitignore", pv.TEXT_MIME),
    ("/a/thing.bin", "application/octet-stream"),
    ("/a/noext", "application/octet-stream"),
])
def test_guess_mime(path, expect):
    assert pv.guess_mime(path) == expect


# ---------- 纯函数：Range 解析 ----------

def test_parse_range_none_and_full():
    assert pv.parse_range(None, 100) is None
    assert pv.parse_range("", 100) is None
    assert pv.parse_range("items=0-5", 100) is None  # 非 bytes 单位：忽略


def test_parse_range_forms():
    assert pv.parse_range("bytes=0-9", 100) == (0, 9)
    assert pv.parse_range("bytes=10-", 100) == (10, 99)
    assert pv.parse_range("bytes=-5", 100) == (95, 99)
    assert pv.parse_range("bytes=0-99999", 100) == (0, 99)  # 尾部越界被钳住
    assert pv.parse_range("bytes=0-9,20-29", 100) == (0, 9)  # 多段只取第一段
    assert pv.parse_range("bytes=-500", 100) == (0, 99)      # 后缀比文件还大


@pytest.mark.parametrize("header", ["bytes=100-200", "bytes=abc", "bytes=", "bytes=5-1", "bytes=10-19"])
def test_parse_range_unsatisfiable(header):
    size = 10 if header == "bytes=10-19" else 100
    with pytest.raises(ValueError):
        pv.parse_range(header, size)


def test_parse_range_empty_file():
    with pytest.raises(ValueError):
        pv.parse_range("bytes=0-9", 0)


# ---------- 端点：真实 paramiko 连 stub server ----------

@pytest.fixture
def stub(tmp_path, monkeypatch):
    """一个连得上 stub SFTP 的书签 + TestClient。"""
    import app.bookmarks as bm
    port, root, _key = run_sftp_server()
    with open(os.path.join(root, "pic.png"), "wb") as f:
        f.write(PAYLOAD)
    with open(os.path.join(root, "code.py"), "w") as f:
        f.write("print('hi')\n")
    os.makedirs(os.path.join(root, "adir"), exist_ok=True)

    monkeypatch.setattr(bm.config, "BOOKMARKS_FILE", tmp_path / "bookmarks.json")
    monkeypatch.setattr("app.main.get_password", lambda a: "pass")
    m.pool.dispose("t")  # 清掉可能残留的会话
    bm.save_bookmarks([{
        "id": "t", "name": "stub", "host": "127.0.0.1", "port": port,
        "username": "user", "auth_method": "password", "default_path": "/",
    }])
    client = TestClient(m.app)
    yield client, root
    m.pool.dispose("t")


def test_preview_full_file_inline(stub):
    client, _root = stub
    r = client.get("/api/bookmarks/t/preview", params={"path": "/pic.png"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-disposition"].startswith("inline")
    assert r.headers["content-length"] == str(len(PAYLOAD))
    assert r.content == PAYLOAD


def test_preview_over_limit_413(stub):
    client, root = stub
    with open(os.path.join(root, "huge.bin"), "wb") as f:
        f.truncate(config.PREVIEW_MAX_BYTES + 1)   # 稀疏文件：只改大小，不落地一个字节
    r = client.get("/api/bookmarks/t/preview", params={"path": "/huge.bin"})
    assert r.status_code == 413
    assert "512 MB" in r.json()["detail"]


def test_preview_at_limit_still_allowed(stub):
    client, root = stub
    with open(os.path.join(root, "edge.bin"), "wb") as f:
        f.truncate(config.PREVIEW_MAX_BYTES)       # 正好等于上限：不拒（判据是「超过」）
    r = client.get("/api/bookmarks/t/preview", params={"path": "/edge.bin"},
                   headers={"Range": "bytes=0-9"})
    assert r.status_code == 206
    assert r.content == b"\x00" * 10


def test_preview_text_mime(stub):
    client, _root = stub
    r = client.get("/api/bookmarks/t/preview", params={"path": "/code.py"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert r.content == b"print('hi')\n"


def test_preview_range_head(stub):
    client, _root = stub
    r = client.get("/api/bookmarks/t/preview", params={"path": "/pic.png"},
                   headers={"Range": "bytes=0-3"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 0-3/{len(PAYLOAD)}"
    assert r.headers["content-length"] == "4"
    assert r.content == PAYLOAD[:4]


def test_preview_range_tail_to_end(stub):
    client, _root = stub
    r = client.get("/api/bookmarks/t/preview", params={"path": "/pic.png"},
                   headers={"Range": "bytes=1000-"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 1000-{len(PAYLOAD)-1}/{len(PAYLOAD)}"
    assert r.content == PAYLOAD[1000:]


def test_preview_range_middle(stub):
    client, _root = stub
    r = client.get("/api/bookmarks/t/preview", params={"path": "/pic.png"},
                   headers={"Range": "bytes=10-19"})
    assert r.status_code == 206
    assert r.content == PAYLOAD[10:20]


def test_preview_range_unsatisfiable_416(stub):
    client, _root = stub
    r = client.get("/api/bookmarks/t/preview", params={"path": "/pic.png"},
                   headers={"Range": "bytes=99999-100000"})
    assert r.status_code == 416
    assert r.headers["content-range"] == f"bytes */{len(PAYLOAD)}"


def test_preview_missing_file_404(stub):
    client, _root = stub
    r = client.get("/api/bookmarks/t/preview", params={"path": "/nope.png"})
    assert r.status_code == 404


def test_preview_dir_400(stub):
    client, _root = stub
    r = client.get("/api/bookmarks/t/preview", params={"path": "/adir"})
    assert r.status_code == 400


def test_preview_unknown_bookmark_404(stub):
    client, _root = stub
    r = client.get("/api/bookmarks/nope/preview", params={"path": "/pic.png"})
    assert r.status_code == 404


# ---------- 通道隔离：预览不能占住传输闸门 ----------

@pytest.fixture
def session(monkeypatch):
    import paramiko
    monkeypatch.setattr("app.config.PREFETCH", False)
    port, root, _key = run_sftp_server()
    with open(os.path.join(root, "big.bin"), "wb") as f:
        f.write(b"x" * (4 * 1024 * 1024))
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(hostname="127.0.0.1", port=port, username="user", password="pass",
                timeout=5, allow_agent=False, look_for_keys=False)
    s = sp.SFTPSession(ssh)
    yield s
    s.close()


def test_preview_channel_is_separate_and_reused(session):
    p1 = session.preview_client()
    assert p1 is not session.client
    assert p1 is not session.transfer_client()
    assert session.preview_client() is p1


def test_preview_stream_keeps_transfer_lane_free(session):
    """预览流进行中，下载必须还能开（否则一边看视频一边下载会卡住）。"""
    gen = m._preview_chunks(session, "/big.bin", 0, None)
    assert next(gen)

    assert session.transfer_gate.acquire(timeout=1.0), "预览占住了传输闸门"
    session.transfer_gate.release()

    dl = m._stream_download(session, "/big.bin")
    assert next(dl)
    dl.close()
    gen.close()


def test_preview_abort_releases_lane(session):
    """客户端取消预览（关窗/按取消）后，预览闸门必须立刻放开。"""
    async def run():
        agen = m._stream_preview(session, "/big.bin", 0, None)
        assert await agen.__anext__()
        await agen.aclose()

    asyncio.run(run())

    assert session.preview_gate.acquire(timeout=1.0), "取消后预览闸门没释放"
    session.preview_gate.release()

    gen = m._preview_chunks(session, "/big.bin", 0, None)
    assert next(gen)  # 取消之后还能再预览
    gen.close()


def test_preview_chunks_seek_and_bound(session):
    """带 start/end 的小范围读：内容和长度都要对。"""
    chunks = list(m._preview_chunks(session, "/big.bin", 1024, 1024 + 99))
    body = b"".join(chunks)
    assert len(body) == 100
    assert body == b"x" * 100
