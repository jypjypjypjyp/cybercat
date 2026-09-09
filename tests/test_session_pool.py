from unittest import mock

import app.session_pool as sp


def _fake_client():
    c = mock.Mock()
    return c


def test_lazy_create_and_reuse():
    pool = sp.SessionPool()
    fake = _fake_client()
    conn_cb = mock.Mock(return_value=fake)
    s1 = pool.get_session("b1", connect_cb=conn_cb)
    s2 = pool.get_session("b1", connect_cb=conn_cb)
    assert s1 is s2
    assert conn_cb.call_count == 1


def test_same_lock_serializes():
    pool = sp.SessionPool()
    fake = _fake_client()
    conn_cb = mock.Mock(return_value=fake)
    s1 = pool.get_session("b1", connect_cb=conn_cb)
    s2 = pool.get_session("b1", connect_cb=conn_cb)
    assert s1.lock is s2.lock


def test_dispose():
    pool = sp.SessionPool()
    fake = _fake_client()
    conn_cb = mock.Mock(return_value=fake)
    s = pool.get_session("b1", connect_cb=conn_cb)
    pool.dispose("b1")
    assert "b1" not in pool._sessions
    assert fake.close.called


def test_close_all():
    pool = sp.SessionPool()
    fake = _fake_client()
    conn_cb = mock.Mock(return_value=fake)
    pool.get_session("b1", connect_cb=conn_cb)
    pool.get_session("b2", connect_cb=conn_cb)
    pool.close_all()
    assert pool._sessions == {}
