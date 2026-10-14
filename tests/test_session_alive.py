from unittest import mock

import paramiko

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


def _ssh_with_sftp(sftp):
    """构造 SSHClient mock：open_sftp() 返回给定的 SFTPClient mock。"""
    ssh = mock.Mock()
    ssh.open_sftp.return_value = sftp
    return ssh


def test_session_alive_true():
    s = sp.SFTPSession(_ssh_with_sftp(_sftp_with_transport(True)))
    assert m._session_alive(s) is True


def test_session_alive_false():
    s = sp.SFTPSession(_ssh_with_sftp(_sftp_with_transport(False)))
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
    # 预置 dead 会话在池子里；重建时 _connect 应返回活连接
    monkeypatch.setattr(m, "_connect", lambda bm: _ssh_with_sftp(alive))

    pool._sessions["b1"] = sp.SFTPSession(_ssh_with_sftp(dead))
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
    pool._sessions["b1"] = sp.SFTPSession(_ssh_with_sftp(alive))
    got = m._get_session("b1")
    assert got.client is alive  # 复用，未重建


def test_with_reconnect_retries_once_on_disconnect(monkeypatch):
    """_with_reconnect：第一次 fn 抛断连异常，dispose 重建后重试一次成功。"""
    pool = sp.SessionPool()
    monkeypatch.setattr(m, "pool", pool)
    bm = {"id": "b1", "host": "h", "port": 22, "username": "u",
          "auth_method": "key", "key_path": "~/.ssh/x"}
    monkeypatch.setattr(m.bookmarks, "get_bookmark", lambda bid: bm)
    monkeypatch.setattr(m, "_connect", lambda bm: _ssh_with_sftp(_sftp_with_transport(True)))

    calls = {"n": 0}
    def fn(s):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("Socket is closed")  # 首次模拟断连
        return mk_ret  # 第二次成功
    mk_ret = "ok"
    # 首次 get_session 时 _connect 返回活连接，但 fn 人为抛断连 → 应重试
    r = m._with_reconnect("b1", fn, "操作")
    assert r == "ok"
    assert calls["n"] == 2
    assert "b1" in pool._sessions


def test_with_reconnect_propagates_auth(monkeypatch):
    """_with_reconnect：认证异常 → 401，不重试。"""
    pool = sp.SessionPool()
    monkeypatch.setattr(m, "pool", pool)
    bm = {"id": "b1", "host": "h", "port": 22, "username": "u",
          "auth_method": "key", "key_path": "~/.ssh/x"}
    monkeypatch.setattr(m.bookmarks, "get_bookmark", lambda bid: bm)
    monkeypatch.setattr(m, "_connect", lambda bm: _ssh_with_sftp(_sftp_with_transport(True)))

    def fn(s):
        raise paramiko.AuthenticationException("bad auth")
    import pytest
    with pytest.raises(Exception) as ei:
        m._with_reconnect("b1", fn, "操作")
    assert ei.value.status_code == 401


def test_with_reconnect_propagates_notfound(monkeypatch):
    """_with_reconnect：FileNotFoundError → 404，不重试。"""
    pool = sp.SessionPool()
    monkeypatch.setattr(m, "pool", pool)
    bm = {"id": "b1", "host": "h", "port": 22, "username": "u",
          "auth_method": "key", "key_path": "~/.ssh/x"}
    monkeypatch.setattr(m.bookmarks, "get_bookmark", lambda bid: bm)
    monkeypatch.setattr(m, "_connect", lambda bm: _ssh_with_sftp(_sftp_with_transport(True)))

    def fn(s):
        raise FileNotFoundError("no path")
    import pytest
    with pytest.raises(Exception) as ei:
        m._with_reconnect("b1", fn, "操作")
    assert ei.value.status_code == 404
