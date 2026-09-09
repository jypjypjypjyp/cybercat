import threading
import time

import app.config as config


class SFTPSession:
    """一次 SFTP 连接 + 整连接互斥锁 + 空闲时间戳。

    paramiko SFTPClient 单实例非线程安全（并发请求会死锁），
    因此所有操作都必须经 self.lock 串行化。
    """

    def __init__(self, client):
        self.client = client
        self.lock = threading.RLock()
        self.last_used = time.time()

    def touch(self):
        self.last_used = time.time()

    def close(self):
        try:
            self.client.close()
        except Exception:
            pass


class SessionPool:
    """每书签一个 SFTP 会话，懒加载 + 复用 + 空闲回收。"""

    def __init__(self):
        self._sessions = {}
        self._lock = threading.Lock()

    def get_session(self, bid, connect_cb=None):
        """取会话；没有则用 connect_cb 建立。connect_cb(bid) -> SFTPClient。"""
        with self._lock:
            s = self._sessions.get(bid)
            if s is None:
                if connect_cb is None:
                    raise RuntimeError(f"no connect callback for bookmark {bid}")
                client = connect_cb(bid)
                s = SFTPSession(client)
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

    def cleanup_idle(self):
        now = time.time()
        with self._lock:
            ids = [bid for bid, s in self._sessions.items() if now - s.last_used > config.IDLE_TIMEOUT]
        for bid in ids:
            self.dispose(bid)
