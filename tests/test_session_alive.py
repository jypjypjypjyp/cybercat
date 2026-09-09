from unittest import mock

import app.main as m
import app.session_pool as sp


def _sftp_with_transport(alive):
    """构造 SFTPClient mock（含 transport.is_active = alive）。"""
    client = mock.Mock()
    ch = mock.Mock()
    t = mock.Mock()
    t.is_active.return_value = alive
    ch.get_transport.return_value = t
    client.get_channel.return_value = ch
    return client


def test_session_alive_true():
    client = _sftp_with_transport(True)
    s = sp.SFTPSession(client)
    assert m._session_alive(s) is True


def test_session_alive_false():
    client = _sftp_with_transport(False)
    s = sp.SFTPSession(client)
    assert m._session_alive(s) is False


def test_session_alive_channel_none():
    s = sp.SFTPSession(mock.Mock())
    s.client.get_channel.return_value = None
    assert m._session_alive(s) is False


def test_get_session_reconnects_if_dead(monkeypatch):
    """连接已死 → dispose 旧会话并重建为活连接。"""
    pool = sp.SessionPool()
    monkeypatch.setattr(m, "pool", pool)
    bm = {"id": "b1", "host": "h", "port": 22, "username": "u",
          "auth_method": "key", "key_path": "~/.ssh/x"}
    monkeypatch.setattr(m.bookmarks, "get_bookmark", lambda bid: bm)

    dead = _sftp_with_transport(False)
    alive = _sftp_with_transport(True)
    # 预置 dead 会话在池子里；重建时 _connect 应返回 alive
    monkeypatch.setattr(m, "_connect", lambda bm: alive)

    pool._sessions["b1"] = sp.SFTPSession(dead)
    got = m._get_session("b1")
    assert got.client is alive  # 死连接被换成活连接
    assert "b1" in pool._sessions


def test_get_session_reuses_alive(monkeypatch):
    """连接活着 → 复用，不重建。"""
    pool = sp.SessionPool()
    monkeypatch.setattr(m, "pool", pool)
    bm = {"id": "b1", "host": "h", "port": 22, "username": "u",
          "auth_method": "key", "key_path": "~/.ssh/x"}
    monkeypatch.setattr(m.bookmarks, "get_bookmark", lambda bid: bm)

    alive = _sftp_with_transport(True)
    pool._sessions["b1"] = sp.SFTPSession(alive)
    got = m._get_session("b1")
    assert got.client is alive  # 复用，未重建
