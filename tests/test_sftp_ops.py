import posixpath
import threading
from unittest import mock

import pytest

import app.sftp_ops as ops


def _mk_session():
    """构造一个带真实 lock 的 session mock。

    shell_dotfiles=False 表示「这台服务器不用点文件兜底」，老用例都按这个前提跑。
    """
    s = mock.Mock()
    s.lock = threading.RLock()
    s.shell_dotfiles = False
    return s


def _attr(name, is_dir=False, size=10, mtime=1):
    from paramiko import SFTPAttributes
    a = SFTPAttributes()
    a.filename = name
    a.st_mode = 0o040755 if is_dir else 0o100644
    a.st_size = size
    a.st_mtime = mtime
    return a


def _session_with_attrs(attr_dict):
    """构造一个带 client.listdir_attr 返回 attr 列表的 session mock。"""
    s = _mk_session()
    attrs = []
    for name, is_dir, size, mtime in attr_dict:
        attrs.append(_attr(name, is_dir, size, mtime))
    s.client.listdir_attr.return_value = attrs
    return s


def _session_with_shell(attr_dict, shell_names):
    """未判定状态（shell_dotfiles=None）+ 指定 shell 返回值的 session mock。"""
    s = _session_with_attrs(attr_dict)
    s.shell_dotfiles = None
    s.shell_names.return_value = shell_names
    return s


def test_list_dir_returns_every_entry():
    """列表必须给全：按时间排序要排整个目录，截断会让最新的文件根本不出现。

    600 个文件、名字升序与时间降序相反时，只截前 500 条（旧行为）就会漏掉最新的那个。
    """
    attrs = [(f"step-{i:06d}.mp4", False, 1024, 1000 + i) for i in range(600)]
    s = _session_with_attrs(attrs)
    r = ops.list_dir(s, "/val")
    assert len(r["entries"]) == 600
    assert r["total"] == 600
    newest = max(r["entries"], key=lambda e: e["mtime"])
    assert newest["name"] == "step-000599.mp4"


def test_list_dir_dirs_first_and_sorted_by_name():
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
    assert [e["name"] for e in r["entries"]] == ["dir", "a.txt", "b.txt", "file.txt"]
    # path 拼接
    assert r["entries"][0]["path"] == "/home/u/dir"


def test_mkdir_and_rename_call_client():
    s = _mk_session()
    ops.mkdir(s, "/home/u/newdir")
    s.client.mkdir.assert_called_once_with("/home/u/newdir")
    ops.rename(s, "/a", "/b")
    s.client.rename.assert_called_once_with("/a", "/b")


def test_delete_recursive_dir_reports_each_item():
    """递归删：目录先 rmdir 试探（非空则列子项），子项按 lstat 判类型后删除。

    进度事件每删掉一项来一条，前端靠它报数。
    """
    s = _mk_session()
    from paramiko import SFTPAttributes
    d = SFTPAttributes(); d.st_mode = 0o040755
    f = SFTPAttributes(); f.st_mode = 0o100644; f.st_size = 7
    s.client.lstat.side_effect = lambda p: d if p == "/home/u/dir" else f
    s.client.listdir_attr.return_value = [f]
    f.filename = "x.txt"
    s.client.rmdir.side_effect = [OSError("not empty"), None]   # 首次非空触发递归，删完后再删
    events = list(ops.delete_tree(s, "/home/u/dir"))
    s.client.listdir_attr.assert_called_once_with("/home/u/dir")
    s.client.remove.assert_called_once_with("/home/u/dir/x.txt")
    assert events == [(False, 7), (True, 0)]


def test_delete_tree_stops_when_cancelled():
    """取消位已置时一个 SFTP 操作都不做。"""
    s = _mk_session()
    stop = threading.Event()
    stop.set()
    with pytest.raises(ops.DeleteCancelled):
        list(ops.delete_tree(s, "/home/u/dir", stop))
    s.client.lstat.assert_not_called()


def test_delete_empty_dir_single_rmdir():
    """空目录不列子项：一次 rmdir 就够。"""
    s = _mk_session()
    from paramiko import SFTPAttributes
    d = SFTPAttributes(); d.st_mode = 0o040755
    s.client.lstat.return_value = d
    events = list(ops.delete_tree(s, "/home/u/empty"))
    s.client.rmdir.assert_called_once_with("/home/u/empty")
    s.client.listdir_attr.assert_not_called()
    assert events == [(True, 0)]


def test_stat():
    s = _mk_session()
    from paramiko import SFTPAttributes
    a = SFTPAttributes(); a.st_mode = 0o100644; a.st_size = 42; a.st_mtime = 99
    s.client.stat.return_value = a
    r = ops.stat(s, "/home/u/file.txt")
    assert r["name"] == "file.txt"
    assert r["is_dir"] is False
    assert r["size"] == 42


def test_list_dir_fills_dotfiles_from_shell():
    """跳板机省略点文件时，用 shell 的 ls -a1 把名字补回来，属性仍由 SFTP 的 stat 提供。"""
    s = _session_with_shell([("README.md", False, 30, 5)], ["README.md", ".gitignore", ".vscode"])
    s.client.stat.side_effect = lambda p: _attr(
        posixpath.basename(p), is_dir=p.endswith(".vscode"), size=7, mtime=9
    )
    r = ops.list_dir(s, "/home/u")
    assert [e["name"] for e in r["entries"]] == [".vscode", ".gitignore", "README.md"]
    assert r["entries"][0]["is_dir"] is True
    assert r["entries"][0]["path"] == "/home/u/.vscode"
    assert r["total"] == 3
    assert s.shell_dotfiles is True


def test_list_dir_skips_shell_when_server_returns_dotfiles():
    """服务器自己就返回点文件：不用兜底，也不该白跑一次 shell。"""
    s = _session_with_shell([(".env", False, 5, 1), ("a.txt", False, 5, 1)], [])
    r = ops.list_dir(s, "/home/u")
    s.shell_names.assert_not_called()
    assert s.shell_dotfiles is False
    assert [e["name"] for e in r["entries"]] == [".env", "a.txt"]


def test_list_dir_gives_up_when_shell_unavailable():
    """shell 跑不动（exec 被拒）就记成补不了，不再每次列目录都试。"""
    s = _session_with_shell([("a.txt", False, 5, 1)], None)
    r = ops.list_dir(s, "/home/u")
    assert [e["name"] for e in r["entries"]] == ["a.txt"]
    assert s.shell_dotfiles is False
    ops.list_dir(s, "/home/u")
    assert s.shell_names.call_count == 1


def test_list_dir_keeps_probing_until_confirmed():
    """shell 也没看见点文件时，区分不出「服务器省略」还是「目录本来没有」，保持未判定。"""
    s = _session_with_shell([("a.txt", False, 5, 1)], ["a.txt"])
    ops.list_dir(s, "/home/u")
    assert s.shell_dotfiles is None
    ops.list_dir(s, "/home/u")
    assert s.shell_names.call_count == 2


def test_list_dir_merges_every_time_after_confirmed():
    """已确认省略点文件的服务器，之后每次列目录都补。"""
    s = _session_with_shell([("a.txt", False, 5, 1)], ["a.txt", ".keep"])
    s.shell_dotfiles = True
    s.client.stat.side_effect = lambda p: _attr(posixpath.basename(p))
    r = ops.list_dir(s, "/home/u")
    assert [e["name"] for e in r["entries"]] == [".keep", "a.txt"]
    assert r["total"] == 2
