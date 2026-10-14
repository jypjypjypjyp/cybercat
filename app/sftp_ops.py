import posixpath
import stat as statmod


def _entry_to_dict(e, base_path):
    isdir = bool(e.st_mode) and statmod.S_ISDIR(e.st_mode)
    return {
        "name": e.filename,
        "path": posixpath.join(base_path, e.filename),
        "is_dir": isdir,
        "size": e.st_size,
        "mtime": e.st_mtime,
        "mode": e.st_mode,
        "permissions": statmod.filemode(e.st_mode) if e.st_mode else "",
    }


def _shell_dot_entries(session, path, names):
    """SFTP 列表里没有点文件时，用 shell 补齐缺的点条目（属性取自 SFTP 的 stat）。

    只认「shell 里有、SFTP 里没有的点名字」：有 → 判定这台服务器省略点文件，之后每次都补；
    一个都没有 → 保持未判定（目录本来没有点文件时区分不出来），下次列目录再问；
    shell 不可用 → 记成补不了，不再问。stat 取不到的候选直接丢掉。
    """
    shell_names = session.shell_names(path)
    if shell_names is None:
        session.shell_dotfiles = False
        return []
    missing = [n for n in shell_names if n.startswith(".") and n not in names]
    if not missing:
        return []
    session.shell_dotfiles = True
    extra = []
    for name in missing:
        try:
            e = session.client.stat(posixpath.join(path, name))
        except OSError:
            continue
        e.filename = name
        extra.append(e)
    return extra


def list_dir(session, path):
    """列表：listdir_attr 一次往返拿全部属性，全部返回。

    不做分页：截断会让前端只能在一部分条目上排序（按时间/大小排不到全目录）。
    跳板机的 SFTP 省略点文件时，用 shell 补齐（见 _shell_dot_entries）。
    """
    with session.lock:
        attrs = list(session.client.listdir_attr(path))
        names = {e.filename for e in attrs}
        if session.shell_dotfiles is not False:
            if any(n.startswith(".") for n in names):
                # 服务器自己就返回点文件，这台不用兜底
                session.shell_dotfiles = False
            else:
                attrs += _shell_dot_entries(session, path, names)
    entries = [_entry_to_dict(e, path) for e in attrs]
    # 目录在前，按名称排序
    entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    return {"entries": entries, "total": len(entries)}


def mkdir(session, path):
    with session.lock:
        session.client.mkdir(path)
    return True


def rename(session, old, new):
    with session.lock:
        session.client.rename(old, new)
    return True


def rmtree(session, path):
    """递归删除（目录/文件）。用于空目录或文件删除。"""
    with session.lock:
        try:
            st = session.client.stat(path)
        except FileNotFoundError:
            return False
        if statmod.S_ISDIR(st.st_mode):
            try:
                session.client.rmdir(path)
                return True
            except OSError:
                # 非空目录：逐项递归删
                for entry in session.client.listdir(path):
                    child = posixpath.join(path, entry)
                    rmtree(session, child)
                session.client.rmdir(path)
                return True
        else:
            try:
                session.client.remove(path)
            except OSError:
                # tar/符号链接等用 remove
                session.client.remove(path)
            return True


def delete(session, path):
    # paramiko 无递归删除，rstat 判断后递归处理
    return rmtree(session, path)


def stat(session, path):
    with session.lock:
        e = session.client.stat(path)
    return {
        "name": posixpath.basename(path),
        "path": path,
        "is_dir": statmod.S_ISDIR(e.st_mode),
        "size": e.st_size,
        "mtime": e.st_mtime,
        "mode": e.st_mode,
        "permissions": statmod.filemode(e.st_mode),
    }
