import time

from fastapi.testclient import TestClient

import app.config as config
import app.main as m
import app.session_pool as sp


class _FakeSession:
    """只需要 cleanup_idle 用到的 last_used 与 close。"""

    def __init__(self, last_used):
        self.last_used = last_used
        self.closed = False

    def close(self):
        self.closed = True


def test_idle_sweeper_reclaims_idle_session(monkeypatch):
    """lifespan 起的后台扫描按 IDLE_TIMEOUT 回收空闲会话，未超时的不动。"""
    monkeypatch.setattr(m, "IDLE_SWEEP_SECONDS", 0.01)
    pool = sp.SessionPool()
    monkeypatch.setattr(m, "pool", pool)
    idle = _FakeSession(last_used=time.time() - config.IDLE_TIMEOUT - 1)
    fresh = _FakeSession(last_used=time.time())
    pool._sessions["idle"] = idle
    pool._sessions["fresh"] = fresh

    with TestClient(m.app):
        deadline = time.time() + 2
        while "idle" in pool._sessions and time.time() < deadline:
            time.sleep(0.02)
        # 断言放在 lifespan 退出前：退出时 close_all() 会清空整个池子
        assert idle.closed is True
        assert "idle" not in pool._sessions
        assert "fresh" in pool._sessions
        assert fresh.closed is False
