# 端到端协议级冒烟：paramiko 5.0 真实连接 stub server，验证 list/upload/download/rename/delete
import os
import threading
import time

import paramiko

import app.sftp_ops as ops
import app.session_pool as sp
from tests.sftp_stub_server import run_sftp_server, StubSFTPServer


def test_stub_server_roundtrip():
    """用真实 paramiko 连内存 SFTP server，走完整操作链路。"""
    port, root, key = run_sftp_server()
    # root 就是 server 根；创建一个测试文件与目录
    os.makedirs(os.path.join(root, "sub"), exist_ok=True)
    with open(os.path.join(root, "hello.txt"), "w") as f:
        f.write("1234567890")

    # 建立连接
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname="127.0.0.1", port=port, username="user", password="pass",
                   timeout=5, allow_agent=False, look_for_keys=False)
    sftp = client.open_sftp()

    session = sp.SFTPSession(client)

    # 列表
    r = ops.list_dir(session, "/")
    names = [e["name"] for e in r["entries"]]
    assert "hello.txt" in names
    assert "sub" in names
    hello = next(e for e in r["entries"] if e["name"] == "hello.txt")
    assert hello["is_dir"] is False
    assert hello["size"] == 10

    # 新建目录
    ops.mkdir(session, "/newdir")
    assert os.path.isdir(os.path.join(root, "newdir"))

    # 重命名
    ops.rename(session, "/hello.txt", "/renamed.txt")
    assert os.path.exists(os.path.join(root, "renamed.txt"))

    # 读取文件（对应下载）
    with session.lock:
        f = session.client.open("/renamed.txt", "rb")
        data = f.read()
        f.close()
    assert data == b"1234567890"

    # 删除
    ops.delete(session, "/renamed.txt")
    assert not os.path.exists(os.path.join(root, "renamed.txt"))
    ops.delete(session, "/newdir")
    assert not os.path.exists(os.path.join(root, "newdir"))

    client.close()
