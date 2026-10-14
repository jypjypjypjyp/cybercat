import asyncio
import os
import posixpath
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import paramiko
from fastapi import FastAPI, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import app.bookmarks as bookmarks
import app.config as config
import app.preview as preview
import app.sftp_ops as sfops
import app.sync as syncer
from app.keychain import get_password, set_password, delete_password
from app.session_pool import SessionPool

pool = SessionPool()

# 空闲会话回收的扫描间隔（秒）；空闲阈值本身是 config.IDLE_TIMEOUT（默认 600s）
IDLE_SWEEP_SECONDS = 60


async def _sweep_idle_sessions():
    """定期回收空闲会话，直到 lifespan 退出时被取消；关闭连接是阻塞 I/O，丢线程池跑。"""
    while True:
        await asyncio.sleep(IDLE_SWEEP_SECONDS)
        await run_in_threadpool(pool.cleanup_idle)


@asynccontextmanager
async def lifespan(app: FastAPI):
    sweeper = asyncio.create_task(_sweep_idle_sessions())
    try:
        yield
    finally:
        sweeper.cancel()
        with suppress(asyncio.CancelledError):
            await sweeper
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
    return client


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


def _index_response() -> HTMLResponse:
    """index.html 里的静态资源带上文件 mtime 当版本号：改完前端，普通刷新就能拿到新界面
    （浏览器缓存是按 URL 认的，URL 不变就可能一直用旧的）。"""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    for name in ("style.css", "app.js"):
        stamp = int((STATIC_DIR / name).stat().st_mtime)
        html = html.replace(f'/static/{name}"', f'/static/{name}?v={stamp}"')
    return HTMLResponse(html)


@app.get("/")
def index():
    return _index_response()


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
def list_files(bid: str, path: str = Query("/")):
    return _with_reconnect(bid, lambda s: sfops.list_dir(s, path), "列表")


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


def _stream_download(session, path):
    """下载流：走传输通道，全程只占传输闸门，控制通道的锁一直空着。"""
    with session.transfer_gate:
        f = session.transfer_client().open(path, "rb")
        try:
            # SFTP 预取（流水线）：默认开启，把串行 32KB 往返变为多请求在途。
            # 实测真实 RTT 下提速 3.7~5.5 倍；可用 CYBERCAT_PREFETCH=0 关闭。
            if config.PREFETCH:
                try:
                    size = f.stat().st_size
                    f.prefetch(file_size=size, max_concurrent_requests=config.PREFETCH_CONCURRENCY)
                except Exception:
                    pass  # 预取失败则退回串行读
            while chunk := f.read(256 * 1024):
                yield chunk
        finally:
            f.close()


async def _stream_body(session, path):
    """把同步下载流包成异步生成器，负责收尾。

    Starlette 的 iterate_in_threadpool 不负责关闭被包的同步生成器，只等垃圾回收；
    浏览器取消下载时这里会收到取消异常，finally 立刻关掉同步生成器，闸门和文件句柄随之释放。
    """
    gen = _stream_download(session, path)
    try:
        async for chunk in iterate_in_threadpool(gen):
            yield chunk
    finally:
        gen.close()


@app.get("/api/bookmarks/{bid}/download")
def download(bid: str, path: str):
    try:
        s = _get_session(bid)
        name = posixpath.basename(path)
        return StreamingResponse(
            _stream_body(s, path), media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )
    except FileNotFoundError:
        raise HTTPException(404, "文件不存在")
    except paramiko.SSHException as e:
        pool.dispose(bid)
        raise HTTPException(500, f"连接中断: {e}")
    except Exception as e:
        raise HTTPException(500, f"下载失败: {e}")


def _preview_chunks(session, path, start, end):
    """预览流：走预览通道（不占传输闸门），从 start 读到 end（含端点）。

    不开预取：预览是小范围随机读，流水线没收益；而且 paramiko 的 prefetch 在
    提前 close 时会漏一个常驻后台线程（预览开关频繁，正好踩这个坑）。
    """
    with session.preview_gate:
        f = session.preview_client().open(path, "rb")
        try:
            if start:
                f.seek(start)
            remaining = None if end is None else end - start + 1
            while remaining is None or remaining > 0:
                want = 256 * 1024 if remaining is None else min(256 * 1024, remaining)
                chunk = f.read(want)
                if not chunk:
                    break
                if remaining is not None:
                    remaining -= len(chunk)
                yield chunk
        finally:
            f.close()


async def _stream_preview(session, path, start, end):
    """异步包装：客户端关掉预览（或按取消）时必须立刻关掉同步生成器。

    同 _stream_body 的理由：Starlette 的 iterate_in_threadpool 不等生成器收尾，
    少了这层，取消后预览闸门会一直被占，同书签后续预览全部卡死。
    """
    gen = _preview_chunks(session, path, start, end)
    try:
        async for chunk in iterate_in_threadpool(gen):
            yield chunk
    finally:
        gen.close()


@app.get("/api/bookmarks/{bid}/preview")
def preview_file(bid: str, path: str, request: Request):
    """预览：inline 输出 + HTTP Range（视频拖动进度、前端只取前 256KB 都靠它）。"""
    try:
        s = _get_session(bid)
        info = sfops.stat(s, path)
        if info["is_dir"]:
            raise HTTPException(400, "目录不能预览")
        size = info["size"]
        if size > config.PREVIEW_MAX_BYTES:
            # 超上限连一个字节都不读；直连流式那条路（音视频/PDF 把地址挂给元素）也由这里挡住
            raise HTTPException(
                413, f"文件超过预览上限 {config.PREVIEW_MAX_BYTES // (1024 * 1024)} MB，请下载后查看"
            )
        mime = preview.guess_mime(path)
        name = posixpath.basename(path)
        headers = {
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-store",
            "Content-Disposition": f'inline; filename="{name}"',
        }
        if mime == "image/svg+xml":
            # SVG 里可能有脚本：预览时当纯静态图处理，不给它任何加载能力
            headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
        try:
            rng = preview.parse_range(request.headers.get("range"), size)
        except ValueError:
            headers["Content-Range"] = f"bytes */{size}"
            raise HTTPException(416, "范围不可满足", headers=headers)
        if rng is None:
            return StreamingResponse(
                _stream_preview(s, path, 0, None), media_type=mime,
                headers={**headers, "Content-Length": str(size)},
            )
        start, end = rng
        return StreamingResponse(
            _stream_preview(s, path, start, end), status_code=206, media_type=mime,
            headers={**headers, "Content-Range": f"bytes {start}-{end}/{size}",
                     "Content-Length": str(end - start + 1)},
        )
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(404, "文件不存在")
    except paramiko.SSHException as e:
        pool.dispose(bid)
        raise HTTPException(500, f"连接中断: {e}")
    except Exception as e:
        raise HTTPException(500, f"预览失败: {e}")


def _upload_file(session, remote, fileobj):
    """上传：同样走传输通道，全程只占传输闸门，控制通道的锁一直空着。"""
    with session.transfer_gate:
        with session.transfer_client().file(remote, "wb") as f:
            while chunk := fileobj.read(256 * 1024):
                f.write(chunk)


@app.post("/api/bookmarks/{bid}/upload")
def upload(bid: str, path: str, file: UploadFile = File(...)):
    try:
        s = _get_session(bid)
        remote = posixpath.join(path, file.filename or "upload")
        _upload_file(s, remote, file.file)
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


@app.get("/api/bookmarks/{bid}/session")
def session_state(bid: str):
    """顶栏链接状态：connected / idle / reclaimed。只读，不建立会话、不碰 last_used。"""
    if not bookmarks.get_bookmark(bid):
        raise HTTPException(404, "书签不存在")
    return {"state": pool.link_state(bid)}


# SPA 回退：前端路由 /{hostname}/{path} 返回 index.html 交给前端 JS 接管。
# 必须放在最后，避免抢占 /api/* 与 /static/*（它们已由更早注册的路由匹配）。
@app.get("/{full_path:path}")
def spa_fallback(full_path: str):
    if full_path.startswith("api"):
        raise HTTPException(404, "Not Found")
    return _index_response()
