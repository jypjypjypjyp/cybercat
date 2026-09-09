import posixpath
import stat as statmod

import app.config as config


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


def list_dir(session, path, limit=None):
    """列表：listdir_attr 一次往返拿全部属性，再分页。"""
    limit = limit or config.DEFAULT_PAGE_SIZE
    with session.lock:
        attrs = session.client.listdir_attr(path)
    entries = [_entry_to_dict(e, path) for e in attrs]
    # 目录在前，按名称排序
    entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    total = len(entries)
    page = entries[:limit]
    has_more = total > limit
    return {"entries": page, "total": total, "has_more": has_more}


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
