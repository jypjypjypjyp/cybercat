"""传输通道与控制通道分离：下载/上传进行时，列目录等控制操作不被阻塞。"""
import asyncio
import io
import os
import threading
import time

import paramiko
import pytest

import app.main as m
import app.sftp_ops as ops
import app.session_pool as sp
from tests.sftp_stub_server import run_sftp_server


@pytest.fixture(autouse=True)
def _no_prefetch(monkeypatch):
    """本文件只关心通道隔离：关掉预取，免得中断时 paramiko 后台预取线程刷警告。"""
    import app.config as config
    monkeypatch.setattr(config, "PREFETCH", False)


@pytest.fixture
def session():
    """真实 paramiko 连内存 SFTP server，一个 8MB 文件用于模拟长传输。"""
    port, root, key = run_sftp_server()
    with open(os.path.join(root, "big.bin"), "wb") as f:
        f.write(b"x" * (8 * 1024 * 1024))
    os.makedirs(os.path.join(root, "sub"), exist_ok=True)

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(hostname="127.0.0.1", port=port, username="user", password="pass",
                timeout=5, allow_agent=False, look_for_keys=False)
    s = sp.SFTPSession(ssh)
    yield s
    s.close()


def test_transfer_channel_is_separate_and_reused(session):
    t1 = session.transfer_client()
    assert t1 is not session.client
    assert session.transfer_client() is t1


def test_download_does_not_block_directory_ops(session):
    """下载流进行中，另一线程列目录必须立刻返回（曾经会一直卡到下载结束）。"""
    gen = m._stream_download(session, "/big.bin")
    assert next(gen)

    done = {}

    def probe():
        t0 = time.time()
        done["total"] = ops.list_dir(session, "/sub")["total"]
        done["dt"] = time.time() - t0

    th = threading.Thread(target=probe, daemon=True)
    th.start()
    th.join(timeout=2.0)
    gen.close()  # 等价于浏览器中断下载
    assert not th.is_alive(), "下载进行中，列目录被卡住了"
    assert done["dt"] < 1.0


def test_download_releases_lane_on_abort(session):
    """客户端中断下载后，传输闸门必须放开，后续下载还能跑。"""
    gen = m._stream_download(session, "/big.bin")
    assert next(gen)
    gen.close()

    assert session.transfer_gate.acquire(timeout=1.0), "中断后传输闸门没释放"
    session.transfer_gate.release()

    gen2 = m._stream_download(session, "/big.bin")
    assert next(gen2)
    gen2.close()


def test_async_body_close_releases_lane(session):
    """客户端取消下载时 Starlette 关掉异步体：闸门必须立刻释放。

    回归点：Starlette 的 iterate_in_threadpool 不负责关闭被包的同步生成器，
    少了 _stream_body 这层包装，取消后闸门会一直被占，同书签后续下载全部卡死。
    """
    async def run():
        agen = m._stream_body(session, "/big.bin")
        assert await agen.__anext__()
        await agen.aclose()

    asyncio.run(run())

    assert session.transfer_gate.acquire(timeout=1.0), "取消后传输闸门没释放"
    session.transfer_gate.release()

    gen = m._stream_download(session, "/big.bin")
    assert next(gen)  # 取消之后还能正常开新传输
    gen.close()


def test_upload_uses_the_transfer_lane(session):
    """上传也排在传输闸门后面：闸门被占时上传要等，列目录照旧畅通。"""
    session.transfer_gate.acquire()  # 模拟另一条传输正在跑
    done = {}

    def do_upload():
        m._upload_file(session, "/up.bin", io.BytesIO(b"y" * 4096))
        done["ok"] = True

    th = threading.Thread(target=do_upload, daemon=True)
    th.start()
    th.join(timeout=0.5)
    assert th.is_alive(), "上传应排队等传输闸门，而不是走控制通道"

    # 传输闸门被占期间，列目录仍是通的
    t0 = time.time()
    ops.list_dir(session, "/")
    assert time.time() - t0 < 1.0

    session.transfer_gate.release()
    th.join(timeout=5)
    assert done.get("ok")
    assert "up.bin" in [e["name"] for e in ops.list_dir(session, "/")["entries"]]


def test_transfers_serialize_on_one_lane(session):
    """同一书签同时只跑一条传输（避免多选下载一起压服务器）。"""
    g1 = m._stream_download(session, "/big.bin")
    assert next(g1)

    out = {}

    def second():
        g2 = m._stream_download(session, "/big.bin")
        out["chunk"] = next(g2)
        g2.close()

    th = threading.Thread(target=second, daemon=True)
    th.start()
    th.join(timeout=0.5)
    assert th.is_alive(), "第二条传输应排队等第一条"

    g1.close()
    th.join(timeout=5)
    assert not th.is_alive()
    assert out["chunk"]
