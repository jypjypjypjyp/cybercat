import shlex
import threading
import time

import app.config as config


def _read_until(chan, marker, timeout):
    """读到 marker 为止，返回 marker 之前的正文；超时或 EOF 返回 None。"""
    buf = ""
    deadline = time.time() + timeout
    while marker not in buf:
        if time.time() > deadline:
            return None
        data = chan.recv(65536)
        if not data:
            return None
        buf += data.decode("utf-8", "replace")
    return buf.split(marker)[0]


class ShellChannel:
    """常驻 shell 通道：开一次交互式通道，之后每条命令用哨兵收尾。

    只在跳板机的 SFTP 列目录省略点文件时用（见 app/sftp_ops.py）。
    路径写成 "$HOME" + SFTP 路径：这类网关的 SFTP 根是家目录，shell 的根是整台机器。
    通道出错即作废，会话重建时换成新通道。
    """

    SENTINEL = "__CYBERCAT_END__"   # 每条命令的结束标记
    READY = "__CYBERCAT_READY__"    # 开场就绪标记
    TIMEOUT = 15

    def __init__(self, ssh):
        self.ssh = ssh
        self._chan = None
        self._broken = False

    def names(self, path):
        """列目录里的名字；通道不可用返回 None。"""
        if self._broken:
            return None
        out = None
        try:
            chan = self._chan or self._open()
            if chan is not None:
                cmd = 'ls -a1 -- "$HOME"' + shlex.quote("/" + path.lstrip("/"))
                chan.send(f"{cmd}; echo {self.SENTINEL}\n")
                out = _read_until(chan, self.SENTINEL, self.TIMEOUT)
        except Exception:
            out = None
        if out is None:
            self._broken = True
            self.close()
            return None
        # "." 与 ".." 不是目录条目；其余文本（含 shell 报错）交给调用方用 stat 甄别
        return [n for n in (line.strip() for line in out.splitlines()) if n and n not in (".", "..")]

    def close(self):
        if self._chan is not None:
            try:
                self._chan.close()
            except Exception:
                pass
            self._chan = None

    def _open(self):
        transport = self.ssh.get_transport()
        if transport is None:
            return None
        chan = transport.open_session(timeout=10)
        chan.settimeout(self.TIMEOUT)
        chan.invoke_shell()
        self._chan = chan
        # 关回显、放宽终端宽度：命令本身和折行的长名字都不该混进结果
        chan.send(f"stty -echo cols 1000 2>/dev/null; echo {self.READY}\n")
        if _read_until(chan, self.READY, self.TIMEOUT) is None:
            raise EOFError("shell 没就绪")
        return chan


class SFTPSession:
    """一条 SSH 连接 + 两条常驻通道 + 预览并行通道 + 空闲时间戳。

    - 控制通道 self.client：目录/增删改，由 self.lock 串行化（都是短事务）。
    - 传输通道 transfer_client()：下载/上传，懒开，由 self.transfer_gate 串行化。
    - 预览通道 open_preview_lane()：每次预览开 PREVIEW_LANES 条，读完即关；
      预览之间由 self.preview_gate 串行化。
    - shell 通道 self.shell：只在 SFTP 列目录省略点文件时用（见 ShellChannel）。

    各通道互不等待：看着视频点下载也不会互相挡住。
    paramiko 的 SFTPClient 单实例非线程安全，所以每条通道由单个线程使用。
    """

    def __init__(self, ssh):
        self.ssh = ssh
        self.client = ssh.open_sftp()
        self.lock = threading.RLock()
        # 传输闸门用 Semaphore 而不是 Lock：传输在生成器里跨线程驱动，
        # Semaphore 允许任意线程释放（Lock/RLock 换线程释放会 RuntimeError）。
        self.transfer_gate = threading.Semaphore(1)
        self.preview_gate = threading.Semaphore(1)
        self._transfer = None
        # 远端 ffmpeg 路径（探测/上传一次后记住）
        self.ffmpeg = None
        self.ffmpeg_lock = threading.Lock()
        # 点文件兜底的三态：None 未判定 / True 服务器省略点文件（每次都补）/ False 不用补或补不了
        self.shell_dotfiles = None
        self.shell = ShellChannel(ssh)
        self.last_used = time.time()

    def transfer_client(self):
        """传输通道（懒开：同一条 SSH 连接再开一个 channel，不重新握手）。

        调用方须先持有 self.transfer_gate，避免并发开通道。
        """
        if self._transfer is None:
            self._transfer = self.ssh.open_sftp()
        return self._transfer

    def open_preview_lane(self):
        """预览并行读的一条独立通道；一次预览结束后由调用方关闭。"""
        return self.ssh.open_sftp()

    def shell_names(self, path):
        """用常驻 shell 通道列目录里的名字（见 ShellChannel）；通道坏了返回 None。"""
        with self.lock:
            return self.shell.names(path)

    def touch(self):
        self.last_used = time.time()

    def close(self):
        self.shell.close()
        for c in (self._transfer, self.client, self.ssh):
            if c is None:
                continue
            try:
                c.close()
            except Exception:
                pass


class SessionPool:
    """每书签一个 SFTP 会话，懒加载 + 复用 + 空闲回收。"""

    def __init__(self):
        self._sessions = {}
        self._lock = threading.Lock()

    def get_session(self, bid, connect_cb=None):
        """取会话；没有则用 connect_cb 建立。connect_cb(bid) -> paramiko SSHClient。"""
        with self._lock:
            s = self._sessions.get(bid)
            if s is None:
                if connect_cb is None:
                    raise RuntimeError(f"no connect callback for bookmark {bid}")
                ssh = connect_cb(bid)
                s = SFTPSession(ssh)
                self._sessions[bid] = s
            s.touch()
            return s

    def dispose(self, bid):
        with self._lock:
            s = self._sessions.pop(bid, None)
        if s:
            s.close()

    def close_all(self):
        with self._lock:
            items = list(self._sessions.items())
            self._sessions.clear()
        for _, s in items:
            s.close()

    def link_state(self, bid):
        """顶栏链接状态：会话在且近期用过 → connected；在但闲置 → idle；不在 → reclaimed。"""
        with self._lock:
            s = self._sessions.get(bid)
        if s is None:
            return "reclaimed"
        if time.time() - s.last_used < config.ACTIVE_WINDOW_SECONDS:
            return "connected"
        return "idle"

    def cleanup_idle(self):
        now = time.time()
        with self._lock:
            ids = [bid for bid, s in self._sessions.items() if now - s.last_used > config.IDLE_TIMEOUT]
        for bid in ids:
            self.dispose(bid)
