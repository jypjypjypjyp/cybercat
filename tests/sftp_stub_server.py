# -*- coding: utf-8 -*-
# 内存 SFTP stub server，用于端到端冒烟测试（参考 paramiko 官方 _stub_sftp.py 精简版）
import os
import socket
import stat
import threading

import paramiko
from paramiko import (
    AUTH_SUCCESSFUL, OPEN_SUCCEEDED, SFTPAttributes, SFTPHandle,
    SFTPServer, SFTPServerInterface, ServerInterface,
)


class StubServer(ServerInterface):
    def check_auth_password(self, username, password):
        return AUTH_SUCCESSFUL

    def check_auth_publickey(self, username, key):
        return AUTH_SUCCESSFUL

    def get_allowed_auths(self, username):
        return "password,publickey"

    def check_channel_request(self, kind, chanid):
        return OPEN_SUCCEEDED


class StubSFTPHandle(SFTPHandle):
    # 可配置的每块读延迟（秒），用于模拟高 RTT 场景做 prefetch 收益实测
    read_delay = 0.0

    def __init__(self, flags):
        super().__init__(flags)
        self.read_delay = StubSFTPHandle.read_delay

    def read(self, offset, length):
        import time as _time
        if self.read_delay:
            _time.sleep(self.read_delay)  # 模拟每块一个 RTT
        try:
            self.readfile.seek(offset)
            data = self.readfile.read(length)
            if isinstance(data, str):
                data = data.encode("utf-8")
            return data
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)

    def stat(self):
        try:
            return self.fstat()
        except OSError as e:
            raise paramiko.SFTPError(e)

    def fstat(self):
        import stat as statmod
        st = os.stat(self.filename)
        a = paramiko.SFTPAttributes()
        a.st_mode = st.st_mode
        a.st_size = st.st_size
        a.st_atime = int(st.st_atime)
        a.st_mtime = int(st.st_mtime)
        return a


class StubSFTPServer(SFTPServerInterface):
    # 共享根目录（由 run_sftp_server 设置）
    shared_root = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.root = StubSFTPServer.shared_root or "/tmp/sftp-stub-root"

    def _real(self, path):
        # 把 SFTP 路径映射到本地 /tmp/sftp-stub-root
        p = path.lstrip("/")
        return os.path.join(self.root, p)

    def list_folder(self, path):
        real = self._real(path)
        try:
            out = []
            for name in os.listdir(real):
                full = os.path.join(real, name)
                st = os.stat(full)
                attrs = SFTPAttributes()
                attrs.filename = name
                attrs.st_mode = st.st_mode
                attrs.st_size = st.st_size
                attrs.st_atime = int(st.st_atime)  # 必须同时设 atime，否则 FLAG_AMTIME 不置位
                attrs.st_mtime = int(st.st_mtime)
                out.append(attrs)
            return out
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)

    def stat(self, path):
        real = self._real(path)
        try:
            st = os.stat(real)
            a = SFTPAttributes()
            a.st_mode = st.st_mode
            a.st_size = st.st_size
            a.st_atime = int(st.st_atime)
            a.st_mtime = int(st.st_mtime)
            return a
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)

    def lstat(self, path):
        return self.stat(path)

    def open(self, path, flags, attr):
        real = self._real(path)
        try:
            if flags & os.O_WRONLY or flags & os.O_RDWR:
                fd = os.open(real, flags | os.O_CREAT, 0o644)
            else:
                fd = os.open(real, flags)
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)
        f = StubSFTPHandle(flags)
        f.filename = real
        f.readfile = os.fdopen(fd, "rb")
        f.writefile = os.fdopen(os.dup(fd), "wb")
        return f

    def remove(self, path):
        real = self._real(path)
        try:
            os.remove(real)
            return paramiko.SFTP_OK
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)

    def rename(self, oldpath, newpath):
        try:
            os.rename(self._real(oldpath), self._real(newpath))
            return paramiko.SFTP_OK
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)

    def mkdir(self, path, attr):
        try:
            os.mkdir(self._real(path))
            return paramiko.SFTP_OK
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)

    def rmdir(self, path):
        try:
            os.rmdir(self._real(path))
            return paramiko.SFTP_OK
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)

    def canonicalize(self, path):
        return os.path.normpath(path)


def _make_rsa_key():
    return paramiko.RSAKey.generate(2048)


def _handle_one(client, key):
    t = paramiko.Transport(client)
    try:
        t.add_server_key(key)
        t.set_subsystem_handler("sftp", SFTPServer, StubSFTPServer)
        t.start_server(server=StubServer())
        while t.is_active():
            import time
            time.sleep(0.2)
    except Exception:
        pass
    finally:
        try:
            t.close()
        except Exception:
            pass


def run_sftp_server():
    """启动一个内存 SFTP server 线程，返回 (host, port, key)。供 TestClient 外的协议级冒烟。"""
    import tempfile
    root = tempfile.mkdtemp(prefix="sftp-stub-root-")
    StubSFTPServer.shared_root = root
    key = _make_rsa_key()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)
    port = sock.getsockname()[1]

    def serve():
        while True:
            try:
                client, addr = sock.accept()
            except OSError:
                return
            # 每个连接独立线程处理，accept 循环不阻塞
            threading.Thread(target=_handle_one, args=(client, key), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    return port, root, key
