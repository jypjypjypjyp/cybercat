"""实时转码：平台识别、命令拼装、流式输出与错误、/transcode 端点。"""
import io
import os

import pytest
from fastapi.testclient import TestClient

import app.main as m
import app.transcode as tc
from tests.sftp_stub_server import run_sftp_server

PAYLOAD = bytes(range(256)) * 4


def test_platform_key():
    assert tc.platform_key("Linux", "x86_64") == "linux-amd64"
    assert tc.platform_key("Linux", "aarch64") == "linux-arm64"
    assert tc.platform_key("Darwin", "arm64") is None
    assert tc.platform_key("Linux", "riscv64") is None


def test_build_command_basic_and_start():
    cmd = tc.build_command("/usr/bin/ffmpeg", "/a/b c.mp4")
    assert cmd.startswith("/usr/bin/ffmpeg -nostdin")
    assert "'/a/b c.mp4'" in cmd            # 路径带空格也要安全
    assert "frag_keyframe+empty_moov" in cmd
    assert "-f mp4 pipe:1" in cmd
    assert "-ss" not in cmd
    assert "-ss 1.500" in tc.build_command("/usr/bin/ffmpeg", "/v.mp4", start=1.5)


class FakeChan:
    def __init__(self, chunks, rc=0, err=b""):
        self.chunks = list(chunks)
        self.rc = rc
        self.err = err
        self.closed = False
        self.command = None
        self.timeout = None

    def settimeout(self, t):
        self.timeout = t

    def exec_command(self, cmd):
        self.command = cmd

    def recv(self, n):
        return self.chunks.pop(0) if self.chunks else b""

    def recv_exit_status(self):
        return self.rc

    def makefile_stderr(self, mode):
        return io.BytesIO(self.err)

    def close(self):
        self.closed = True


def _session(chan):
    transport = type("T", (), {"open_session": lambda self, timeout=None: chan})()
    ssh = type("S", (), {"get_transport": lambda self: transport})()
    return type("Session", (), {"ssh": ssh})()


@pytest.fixture
def fake_remote(monkeypatch):
    monkeypatch.setattr(tc, "ensure_ffmpeg", lambda session: "/usr/bin/ffmpeg")


def test_open_stream_returns_first_chunk_and_rest(fake_remote):
    chan = FakeChan([b"aa", b"bb"])
    first, gen = tc.open_stream(_session(chan), "/remote/v.mp4")
    assert first == b"aa"
    assert list(gen) == [b"bb"]
    assert "/usr/bin/ffmpeg" in chan.command and 'P="$HOME"' in chan.command
    assert chan.closed                       # 读完就关通道，远端 ffmpeg 随之退出


def test_open_stream_raises_with_ffmpeg_error(fake_remote):
    chan = FakeChan([], rc=1, err=b"Invalid data found when processing input")
    with pytest.raises(tc.TranscodeError) as exc:
        tc.open_stream(_session(chan), "/remote/v.mp4")
    assert "Invalid data" in str(exc.value)
    assert chan.closed


def test_close_releases_remote(fake_remote):
    chan = FakeChan([b"aa", b"bb", b"cc"])
    first, gen = tc.open_stream(_session(chan), "/remote/v.mp4")
    next(gen)
    gen.close()
    assert chan.closed


# ---------- 端点：真实 SFTP 连 stub，转码流用假数据 ----------

@pytest.fixture
def stub(tmp_path, monkeypatch):
    import app.bookmarks as bm
    port, root, _key = run_sftp_server()
    with open(os.path.join(root, "v.mp4"), "wb") as f:
        f.write(PAYLOAD)

    monkeypatch.setattr(bm.config, "BOOKMARKS_FILE", tmp_path / "bookmarks.json")
    monkeypatch.setattr("app.main.get_password", lambda a: "pass")
    m.pool.dispose("t")
    bm.save_bookmarks([{
        "id": "t", "name": "stub", "host": "127.0.0.1", "port": port,
        "username": "user", "auth_method": "password", "default_path": "/",
    }])
    yield TestClient(m.app), root
    m.pool.dispose("t")


def test_transcode_endpoint_streams_mp4(stub, monkeypatch):
    client, _root = stub
    def opened(session, path, start=0.0):
        def rest():
            yield b"tail"
        return b"head", rest()
    monkeypatch.setattr(m.transcode, "open_stream", opened)
    r = client.get("/api/bookmarks/t/transcode", params={"path": "/v.mp4"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("video/mp4")
    assert r.content == b"headtail"


def test_transcode_endpoint_reports_failure(stub, monkeypatch):
    client, _root = stub
    def boom(session, path, start=0.0):
        raise tc.TranscodeError("服务器没有 ffmpeg")
    monkeypatch.setattr(m.transcode, "open_stream", boom)
    r = client.get("/api/bookmarks/t/transcode", params={"path": "/v.mp4"})
    assert r.status_code == 500
    assert "服务器没有 ffmpeg" in r.json()["detail"]


def test_transcode_endpoint_rejects_dir(stub):
    client, root = stub
    os.makedirs(os.path.join(root, "adir"), exist_ok=True)
    r = client.get("/api/bookmarks/t/transcode", params={"path": "/adir"})
    assert r.status_code == 400


# ---------- ffmpeg 探测：跳板机会丢退出码，只能看输出 ----------
class ProbeSession:
    """只满足 ensure_ffmpeg 用到的字段：normalize / transfer_client。"""
    def __init__(self):
        self.ffmpeg = None
        import threading
        self.ffmpeg_lock = threading.Lock()
        self.client = self
        self._stat = None

    def normalize(self, path):
        return "/"

    def transfer_client(self):
        return self

    def stat(self, path):
        return self._stat


def test_ensure_ffmpeg_uses_existing_binary(monkeypatch):
    def fake_run(session, command, timeout=20):
        return 0, b"SYS=/usr/bin/ffmpeg\nOS=Linux x86_64\n"

    monkeypatch.setattr(tc, "_run", fake_run)
    assert tc.ensure_ffmpeg(ProbeSession()) == "/usr/bin/ffmpeg"


def test_ensure_ffmpeg_ignores_broken_portable(monkeypatch):
    """跳板机退出码恒 0：便携版看起来可执行，但 -version 跑不出来就不能信。"""

    def fake_run(session, command, timeout=20):
        if "SYS=" in command:
            return 0, (
                b"SYS=\nOS=Linux x86_64\n"
                b"OK-linux-amd64=/home/u/.cache/cybercatoss/ffmpeg-linux-amd64\n"
                b"VER-linux-amd64=bash: ...: Permission denied\n"
            )
        return 0, b"bash: ...: Permission denied\n"

    monkeypatch.setattr(tc, "_run", fake_run)
    session = ProbeSession()
    session._stat = type("S", (), {"st_size": 79 * 1024 * 1024})()
    monkeypatch.setattr(tc, "local_binary", lambda key: __import__("pathlib").Path("/tmp/fake"))
    monkeypatch.setattr(tc, "_upload", lambda session, local, remote: None)
    with pytest.raises(tc.TranscodeError) as exc:
        tc.ensure_ffmpeg(session)
    assert "跑不起来" in str(exc.value)


def test_ensure_ffmpeg_accepts_working_portable(monkeypatch):
    def fake_run(session, command, timeout=20):
        return 0, (
            b"SYS=\nOS=Linux x86_64\n"
            b"OK-linux-amd64=/home/u/.cache/cybercatoss/ffmpeg-linux-amd64\n"
            b"VER-linux-amd64=ffmpeg version 7.0.2-static Copyright\n"
        )

    monkeypatch.setattr(tc, "_run", fake_run)
    assert tc.ensure_ffmpeg(ProbeSession()) == "/home/u/.cache/cybercatoss/ffmpeg-linux-amd64"



def test_ensure_ffmpeg_no_upload_returns_none(monkeypatch):
    """后台预热只探测不传：没装 ffmpeg 的机器不能因为看目录就传 80MB。"""

    def fake_run(session, command, timeout=20):
        if "SYS=" in command:
            return 0, b"SYS=\nOS=Linux x86_64\n"
        return 0, b""

    uploaded = []
    monkeypatch.setattr(tc, "_run", fake_run)
    monkeypatch.setattr(tc, "_upload", lambda *a: uploaded.append(a))
    assert tc.ensure_ffmpeg(ProbeSession(), upload=False) is None
    assert uploaded == []


def test_warm_is_idempotent(monkeypatch):
    session = ProbeSession()
    calls = []

    def fake_ensure(s, upload=True):
        calls.append(upload)
        return "/usr/bin/ffmpeg"

    monkeypatch.setattr(tc, "ensure_ffmpeg", fake_ensure)
    tc.warm(session)
    tc.warm(session)
    import time as _t
    _t.sleep(0.2)
    assert calls == [False]
