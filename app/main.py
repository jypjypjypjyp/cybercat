import os
import posixpath
from contextlib import asynccontextmanager
from pathlib import Path

import paramiko
from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import app.bookmarks as bookmarks
import app.config as config
import app.sftp_ops as sfops
import app.sync as syncer
from app.keychain import get_password, set_password, delete_password
from app.session_pool import SessionPool

pool = SessionPool()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    pool.close_all()


app = FastAPI(title="SFTP Manager", lifespan=lifespan)


# ---------- 真实连接 ----------
def _connect(bookmark):
    host = bookmark["host"]
    port = int(bookmark.get("port", 22))
    user = bookmark["username"]
    method = bookmark.get("auth_method", "key")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    if method == "key":
        key_path = os.path.expanduser(bookmark.get("key_path") or "~/.ssh/id_ed25519")
        passphrase = get_password(f"bookmark:{bookmark['id']}") or None
        client.connect(
            hostname=host, port=port, username=user,
            key_filename=key_path, passphrase=passphrase,
            timeout=10, allow_agent=True, look_for_keys=False,
        )
    else:  # password
        pwd = get_password(f"bookmark:{bookmark['id']}")
        if not pwd:
            raise HTTPException(400, "密码未保存到钥匙串")
        client.connect(
            hostname=host, port=port, username=user,
            password=pwd, timeout=10, allow_agent=True, look_for_keys=False,
        )
    # SSH 保活：每 30s 发 keepalive，防止服务器/网络设备因空闲断连
    try:
        client.get_transport().set_keepalive(30)
    except Exception:
        pass
    return client.open_sftp()


def _session_alive(s):
    """判断 SFTP 会话底层 transport 是否仍活跃（未断连）。"""
    try:
        ch = s.client.get_channel()
        if ch is None:
            return False
        t = ch.get_transport()
        if t is None:
            return False
        return bool(t.is_active())
    except Exception:
        return False


def _get_session(bid):
    """取会话；若底层连接已死，则自动断开并重建（刷新/操作时热重连）。"""
    bm = bookmarks.get_bookmark(bid)
    if not bm:
        raise HTTPException(404, "书签不存在")
    s = pool.get_session(bid, connect_cb=lambda _: _connect(bm))
    if not _session_alive(s):
        pool.dispose(bid)
        s = pool.get_session(bid, connect_cb=lambda _: _connect(bm))
    return s


# ---------- 静态托管 ----------
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


# ---------- 书签 ----------
class BookmarkIn(BaseModel):
    name: str
    host: str
    port: int = 22
    username: str
    auth_method: str = "key"
    key_path: str | None = None
    default_path: str = "/"
    password: str | None = None


@app.get("/api/bookmarks")
def list_bookmarks():
    return bookmarks.load_bookmarks()


@app.post("/api/sync-ssh-config")
def sync_ssh_config():
    """从 ~/.ssh/config 同步到书签：存在的覆盖，没有的新建。"""
    try:
        result = syncer.sync_from_ssh_config()
        return {"ok": True, **result}
    except Exception as e:
        raise HTTPException(500, f"同步失败: {e}")


@app.post("/api/bookmarks", status_code=201)
def create_bookmark(b: BookmarkIn):
    bm = bookmarks.add_bookmark(b.model_dump(exclude={"password"}))
    if b.password:
        set_password(f"bookmark:{bm['id']}", b.password)
    return bm


@app.put("/api/bookmarks/{bid}")
def update_bookmark(bid: str, b: BookmarkIn):
    bm = bookmarks.update_bookmark(bid, b.model_dump(exclude={"password"}))
    if not bm:
        raise HTTPException(404, "书签不存在")
    if b.password:
        set_password(f"bookmark:{bid}", b.password)
    return bm


@app.delete("/api/bookmarks/{bid}", status_code=204)
def del_bookmark(bid: str):
    pool.dispose(bid)
    delete_password(f"bookmark:{bid}")
    bookmarks.delete_bookmark(bid)


@app.post("/api/bookmarks/{bid}/test")
def test_bookmark(bid: str):
    try:
        s = pool.get_session(bid, connect_cb=lambda _: _connect(bookmarks.get_bookmark(bid)))
        s.close()
        pool.dispose(bid)
        return {"ok": True, "message": "连接成功"}
    except paramiko.AuthenticationException:
        pool.dispose(bid)
        return {"ok": False, "message": "认证失败，请检查密钥或密码"}
    except (paramiko.SSHException, paramiko.ssh_exception.NoValidConnectionsError) as e:
        pool.dispose(bid)
        return {"ok": False, "message": f"SSH 错误: {e}"}
    except Exception as e:
        pool.dispose(bid)
        return {"ok": False, "message": f"无法连接: {e}"}


# ---------- 文件操作 ----------
def _is_disconnect(e):
    """判断异常是否为连接中断（可重试）。"""
    return isinstance(e, (paramiko.SSHException, EOFError, OSError))


def _with_reconnect(bid, fn, error_prefix):
    """执行 fn(session)；若连接中断则 dispose + 重建 + 重试一次。

    fn 抛出的非断连异常（认证/路径不存在/其他）会被正确映射或原样冒泡。
    download/upload 是流式端点，不适用本 helper（见其独立实现）。
    """
    try:
        s = _get_session(bid)
        return fn(s)
    except paramiko.AuthenticationException:
        pool.dispose(bid)
        raise HTTPException(401, "认证失败")
    except FileNotFoundError:
        raise HTTPException(404, "路径不存在")
    except Exception as e:
        if not _is_disconnect(e):
            raise
        # 连接中断：丢弃旧会话，重建重试一次
        pool.dispose(bid)
        try:
            s = _get_session(bid)
            return fn(s)
        except paramiko.AuthenticationException:
            pool.dispose(bid)
            raise HTTPException(401, "认证失败")
        except FileNotFoundError:
            raise HTTPException(404, "路径不存在")
        except Exception as e2:
            pool.dispose(bid)
            raise HTTPException(500, f"{error_prefix}: 连接中断且重连失败: {e2}")


@app.get("/api/bookmarks/{bid}/list")
def list_files(bid: str, path: str = Query("/"), limit: int = Query(config.DEFAULT_PAGE_SIZE)):
    return _with_reconnect(bid, lambda s: sfops.list_dir(s, path, limit=limit), "列表")


@app.post("/api/bookmarks/{bid}/mkdir")
def mkdir(bid: str, path: str):
    try:
        return _with_reconnect(bid, lambda s: sfops.mkdir(s, path), "新建目录")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"新建目录失败: {e}")


@app.post("/api/bookmarks/{bid}/rename")
def rename(bid: str, old: str, new: str):
    try:
        return _with_reconnect(bid, lambda s: sfops.rename(s, old, new), "重命名")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"重命名失败: {e}")


@app.post("/api/bookmarks/{bid}/delete")
def delete(bid: str, path: str):
    try:
        return _with_reconnect(bid, lambda s: sfops.delete(s, path), "删除")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"删除失败: {e}")


@app.get("/api/bookmarks/{bid}/download")
def download(bid: str, path: str):
    try:
        s = _get_session(bid)

        def gen():
            with s.lock:
                f = s.client.open(path, "rb")
                try:
                    # 提速：SFTP 预取。先显式 stat 拿 size（避免部分服务器 stat hangs），
                    # 再让后台线程并发抓取数据块（多块在飞行，消除串行 RTT）。
                    try:
                        size = f.stat().st_size
                    except Exception:
                        size = None
                    try:
                        f.prefetch(file_size=size, max_concurrent_requests=16)
                    except Exception:
                        pass  # 预取失败则退回串行读
                    while chunk := f.read(256 * 1024):
                        yield chunk
                finally:
                    f.close()

        name = posixpath.basename(path)
        return StreamingResponse(
            gen(), media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )
    except FileNotFoundError:
        raise HTTPException(404, "文件不存在")
    except paramiko.SSHException as e:
        pool.dispose(bid)
        raise HTTPException(500, f"连接中断: {e}")
    except Exception as e:
        raise HTTPException(500, f"下载失败: {e}")


@app.post("/api/bookmarks/{bid}/upload")
def upload(bid: str, path: str, file: UploadFile = File(...)):
    try:
        s = _get_session(bid)
        remote = posixpath.join(path, file.filename or "upload")
        with s.lock:
            with s.client.file(remote, "wb") as f:
                while chunk := file.file.read(256 * 1024):
                    f.write(chunk)
        return {"ok": True, "path": remote}
    except paramiko.SSHException as e:
        pool.dispose(bid)
        raise HTTPException(500, f"连接中断: {e}")
    except Exception as e:
        raise HTTPException(500, f"上传失败: {e}")


@app.post("/api/bookmarks/{bid}/connect")
def connect(bid: str):
    try:
        _get_session(bid)
        return {"ok": True}
    except Exception as e:
        pool.dispose(bid)
        raise HTTPException(500, f"连接失败: {e}")


# SPA 回退：前端路由 /{hostname}/{path} 返回 index.html 交给前端 JS 接管。
# 必须放在最后，避免抢占 /api/* 与 /static/*（它们已由更早注册的路由匹配）。
@app.get("/{full_path:path}")
def spa_fallback(full_path: str):
    if full_path.startswith("api"):
        raise HTTPException(404, "Not Found")
    return FileResponse(STATIC_DIR / "index.html")
