import io
import time
from unittest import mock

import app.config as config
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


def test_link_state_reclaimed_when_no_session():
    """池子里没有会话 → 已回收。"""
    pool = sp.SessionPool()
    assert pool.link_state("b1") == "reclaimed"


def test_link_state_connected_when_recently_used():
    """会话在，且刚建（1 分钟活跃窗口内）→ 已连接。"""
    pool = sp.SessionPool()
    pool.get_session("b1", connect_cb=mock.Mock(return_value=_fake_client()))
    assert pool.link_state("b1") == "connected"


def test_link_state_idle_past_active_window():
    """会话在，但已超过活跃窗口没用过 → 空闲。"""
    pool = sp.SessionPool()
    s = pool.get_session("b1", connect_cb=mock.Mock(return_value=_fake_client()))
    s.last_used = time.time() - config.ACTIVE_WINDOW_SECONDS - 1
    assert pool.link_state("b1") == "idle"


class _FakeChan:
    """假 shell 通道：按块吐输出，记录收到的命令。"""

    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.sent = []
        self.closed = False

    def send(self, data):
        self.sent.append(data)

    def recv(self, n):
        if not self.chunks:
            raise EOFError("没有更多输出")
        return self.chunks.pop(0)

    def settimeout(self, t):
        pass

    def invoke_shell(self):
        pass

    def close(self):
        self.closed = True


def _shell_with_chunks(chunks):
    chan = _FakeChan(chunks)
    transport = mock.Mock()
    transport.open_session.return_value = chan
    ssh = mock.Mock()
    ssh.get_transport.return_value = transport
    return sp.ShellChannel(ssh), chan, transport


def test_shell_names_reuses_one_channel():
    """常驻 shell：开场读掉横幅，之后一条命令一个哨兵，不再开第二条通道。

    路径前缀必须是 "$HOME"——跳板机的 SFTP 根是家目录、shell 的根是整台机器。
    """
    shell, chan, transport = _shell_with_chunks([
        b"Welcome to Ubuntu 24.04\n__CYBERCAT_READY__\n",
        b".\n..\n.gitignore\na.txt\n__CYBERCAT_END__\n",
        b".keep\n__CYBERCAT_END__\n",
    ])
    assert shell.names("/a b") == [".gitignore", "a.txt"]
    assert shell.names("/") == [".keep"]
    assert transport.open_session.call_count == 1
    assert chan.sent[1] == "ls -a1 -- \"$HOME\"'/a b'; echo __CYBERCAT_END__\n"
    assert chan.sent[2] == 'ls -a1 -- "$HOME"/; echo __CYBERCAT_END__\n'


def test_shell_names_gives_up_after_failure():
    """通道出错就作废：返回 None，且不再重试（不重复付几秒的开销）。"""
    shell, chan, _ = _shell_with_chunks([b"__CYBERCAT_READY__\n"])
    assert shell.names("/x") is None
    sent = len(chan.sent)
    assert shell.names("/x") is None
    assert len(chan.sent) == sent
    assert chan.closed


def test_shell_names_none_when_no_transport():
    ssh = mock.Mock()
    ssh.get_transport.return_value = None
    assert sp.ShellChannel(ssh).names("/x") is None


def test_sftp_session_shell_names_delegates_to_channel():
    s = sp.SFTPSession(mock.Mock())
    s.shell = mock.Mock()
    s.shell.names.return_value = [".env"]
    assert s.shell_names("/x") == [".env"]
    s.shell.names.assert_called_once_with("/x")


def test_close_closes_shell_channel():
    s = sp.SFTPSession(mock.Mock())
    s.shell = mock.Mock()
    s.close()
    assert s.shell.close.called
