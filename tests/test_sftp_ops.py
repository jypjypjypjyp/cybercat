import posixpath
import threading
from unittest import mock

import app.sftp_ops as ops


def _mk_session():
    """构造一个带真实 lock 的 session mock。"""
    s = mock.Mock()
    s.lock = threading.RLock()
    return s


def _session_with_attrs(attr_dict):
    """构造一个带 client.listdir_attr 返回 attr 列表的 session mock。"""
    s = _mk_session()
    from paramiko import SFTPAttributes
    attrs = []
    for name, is_dir, size, mtime in attr_dict:
        a = SFTPAttributes()
        a.filename = name
        a.st_mode = 0o040755 if is_dir else 0o100644
        a.st_size = size
        a.st_mtime = mtime
        attrs.append(a)
    s.client.listdir_attr.return_value = attrs
    return s


def test_list_dir_dirs_first_and_paginate(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DEFAULT_PAGE_SIZE", 2)
    s = _session_with_attrs([
        ("b.txt", False, 10, 1),
        ("file.txt", False, 20, 2),
        ("dir", True, 0, 3),
        ("a.txt", False, 30, 4),
    ])
    r = ops.list_dir(s, "/home/u")
    # 目录在前
    assert r["entries"][0]["name"] == "dir"
    assert r["entries"][0]["is_dir"] is True
    assert r["total"] == 4
    assert r["has_more"] is True
    assert len(r["entries"]) == 2  # 分页 limit=2
    # path 拼接
    assert r["entries"][0]["path"] == "/home/u/dir"


def test_list_dir_respects_limit_full(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DEFAULT_PAGE_SIZE", 100)
    s = _session_with_attrs([("a.txt", False, 1, 1)])
    r = ops.list_dir(s, "/")
    assert r["total"] == 1
    assert r["has_more"] is False


def test_mkdir_and_rename_call_client():
    s = _mk_session()
    ops.mkdir(s, "/home/u/newdir")
    s.client.mkdir.assert_called_once_with("/home/u/newdir")
    ops.rename(s, "/a", "/b")
    s.client.rename.assert_called_once_with("/a", "/b")


def test_delete_recursive_dir():
    s = _mk_session()
    from paramiko import SFTPAttributes
    d = SFTPAttributes(); d.st_mode = 0o040755
    f = SFTPAttributes(); f.st_mode = 0o100644
    def stat_side(p):
        return d if p == "/home/u/dir" else f
    s.client.stat.side_effect = stat_side
    s.client.listdir.return_value = ["x.txt"]
    s.client.rmdir.side_effect = [OSError("not empty"), None, None]  # 首次非空触发递归，后续成功
    ops.delete(s, "/home/u/dir")
    # stat 被调用：先目录，再 x.txt
    s.client.stat.assert_any_call("/home/u/dir")
    s.client.stat.assert_any_call("/home/u/dir/x.txt")
    s.client.remove.assert_called_once_with("/home/u/dir/x.txt")


def test_stat():
    s = _mk_session()
    from paramiko import SFTPAttributes
    a = SFTPAttributes(); a.st_mode = 0o100644; a.st_size = 42; a.st_mtime = 99
    s.client.stat.return_value = a
    r = ops.stat(s, "/home/u/file.txt")
    assert r["name"] == "file.txt"
    assert r["is_dir"] is False
    assert r["size"] == 42
