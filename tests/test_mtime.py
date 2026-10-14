# mtime 字段专项验证：证明 paramiko 5.0 链路能正确传输 st_mtime。
# 早期列表里 mtime 恒为 null，经核对 paramiko SFTPAttributes.unpack 源码，
# 根因是 stub server 只设 st_mtime、漏设 st_atime，导致 FLAG_AMTIME 不置位。
# 本测试在 stub 补上 st_atime 后，实测客户端能读到非 null 的 st_mtime。
import os

import paramiko

import app.sftp_ops as ops
import app.session_pool as sp
from tests.sftp_stub_server import run_sftp_server


def test_mtime_roundtrip_not_null():
    port, root, key = run_sftp_server()
    with open(os.path.join(root, "hello.txt"), "w") as f:
        f.write("hello mtime")
    real_mtime = int(os.stat(os.path.join(root, "hello.txt")).st_mtime)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname="127.0.0.1", port=port, username="user", password="pass",
                   timeout=5, allow_agent=False, look_for_keys=False)
    session = sp.SFTPSession(client)

    r = ops.list_dir(session, "/")
    hello = next(e for e in r["entries"] if e["name"] == "hello.txt")
    # mtime 必须非 null（修复前恒为 None）
    assert hello["mtime"] is not None, "mtime 不应为 null"
    assert abs(hello["mtime"] - real_mtime) < 5, f"mtime {hello['mtime']} != {real_mtime}"
    # 也可通过显式 stat 验证
    st = ops.stat(session, "/hello.txt")
    assert st["mtime"] is not None
    assert abs(st["mtime"] - real_mtime) < 5

    client.close()
