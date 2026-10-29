import asyncio
import json
import os
import posixpath
import threading
import time
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
import app.settings as settings
import app.sync as syncer
import app.transcode as transcode
from app.keychain import get_password, set_password, delete_password
from app.session_pool import SessionPool

settings.load()

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
    transcode.warm(s)   # 后台探测 ffmpeg，首次转码少等一次
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


class DeleteIn(BaseModel):
    paths: list[str]


DELETE_TICK = 0.15   # 进度事件最短间隔（秒）：再密只是白占带宽


def _ndjson(obj):
    return json.dumps(obj, ensure_ascii=False) + "\n"


def _err_text(e):
    """把常见系统错误翻成一句人话；其余原样带上，方便排查。"""
    if getattr(e, "errno", None) in (1, 13):      # EPERM / EACCES
        return "权限不足"
    return str(e)


def _delete_stream(session, paths, stop):
    """逐项删除，边删边发 NDJSON 事件。

    单项失败不影响其余项；连接真断了才整体停下（后面每项都必然失败）。
    计数在服务端累计，前端只负责显示，不需要自己数。
    """
    files = dirs = freed = 0
    failed = []
    last = 0.0
    total = len(paths)
    yield _ndjson({"type": "start", "total": total})
    for idx, path in enumerate(paths, 1):
        if stop.is_set():
            break
        yield _ndjson({"type": "item_start", "index": idx, "total": total, "path": path})
        try:
            for is_dir, size in sfops.delete_tree(session, path, stop):
                if is_dir:
                    dirs += 1
                else:
                    files += 1
                    freed += size
                now = time.time()
                if now - last >= DELETE_TICK:
                    last = now
                    yield _ndjson({"type": "progress", "files": files, "dirs": dirs,
                                   "bytes": freed, "path": path})
            yield _ndjson({"type": "item_done", "index": idx, "path": path, "ok": True})
        except sfops.DeleteCancelled:
            yield _ndjson({"type": "item_done", "index": idx, "path": path,
                           "ok": False, "cancelled": True})
            break
        except FileNotFoundError:
            # 删到一半被别人/另一批删掉了：结果与成功一致
            yield _ndjson({"type": "item_done", "index": idx, "path": path,
                           "ok": True, "missing": True})
        except Exception as e:
            failed.append({"path": path, "error": _err_text(e)})
            yield _ndjson({"type": "item_done", "index": idx, "path": path,
                           "ok": False, "error": _err_text(e)})
            if isinstance(e, (paramiko.SSHException, EOFError)):
                yield _ndjson({"type": "aborted", "error": "连接中断，已停止"})
                break
    yield _ndjson({"type": "done", "files": files, "dirs": dirs, "bytes": freed,
                   "failed": failed, "cancelled": stop.is_set()})


async def _delete_body(session, paths, stop):
    """同步删除流包成异步；浏览器断开（点取消/关页面）时置取消位并收掉生成器。

    删除逐条检查取消位，断开后最多再删完当前一条。
    """
    gen = _delete_stream(session, paths, stop)
    try:
        async for line in iterate_in_threadpool(gen):
            yield line
    finally:
        stop.set()
        try:
            gen.close()
        except (RuntimeError, ValueError):
            pass    # 生成器还在线程池里跑当前一条：取消位已置，它出来后会自己收尾


@app.post("/api/bookmarks/{bid}/delete")
def delete(bid: str, req: DeleteIn):
    try:
        s = _get_session(bid)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"删除失败: {e}")
    return StreamingResponse(
        _delete_body(s, req.paths, threading.Event()),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


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


PREVIEW_BLOCK = 256 * 1024      # 预览每次递给浏览器的分片大小


def _preview_chunks(session, path, start, end, size):
    """预览流：从 start 读到 end（含端点），按块分给多条通道并行读。"""
    total = size - start if end is None else end - start + 1
    if total <= 0:
        return
    blocks = (total + PREVIEW_BLOCK - 1) // PREVIEW_BLOCK

    with session.preview_gate:
        pairs = []
        err = None
        for _ in range(min(config.PREVIEW_LANES, blocks)):
            client = None
            try:
                client = session.open_preview_lane()
                pairs.append((client, client.open(path, "rb")))
            except Exception as exc:
                err = exc
                if client is not None:
                    try:
                        client.close()
                    except Exception:
                        pass
                break
        if not pairs:
            raise err
        cond = threading.Condition()
        stop = threading.Event()
        buf = {}            # 块序号 → 数据（或异常）
        issued = 0          # 已派发的块数
        sent = 0            # 已下发的块数
        alive = len(pairs)  # 还在读的通道数

        def lane(client, f):
            nonlocal issued, alive
            try:
                while True:
                    with cond:
                        while not stop.is_set() and issued - sent >= len(pairs):
                            cond.wait()
                        if stop.is_set() or issued >= blocks:
                            return
                        idx = issued
                        issued += 1
                    off = idx * PREVIEW_BLOCK
                    want = min(PREVIEW_BLOCK, total - off)
                    try:
                        f.seek(start + off)
                        data = f.read(want)
                    except Exception as exc:
                        data = exc
                    with cond:
                        buf[idx] = data
                        cond.notify_all()
                    if isinstance(data, Exception) or len(data) < want:
                        return
            finally:
                with cond:
                    alive -= 1
                    cond.notify_all()
                for res in (f, client):
                    try:
                        res.close()
                    except Exception:
                        pass

        threads = [threading.Thread(target=lane, args=p, daemon=True, name="preview-lane") for p in pairs]
        try:
            for t in threads:
                t.start()
            while True:
                with cond:
                    while sent not in buf and alive:
                        cond.wait()
                    if sent not in buf:
                        return
                    data = buf.pop(sent)
                    idx, sent = sent, sent + 1
                    cond.notify_all()
                if isinstance(data, Exception):
                    raise data
                session.touch()   # 每发一块续一次活跃时间
                yield data
                if len(data) < min(PREVIEW_BLOCK, total - idx * PREVIEW_BLOCK):
                    return
        finally:
            with cond:
                stop.set()
                cond.notify_all()
            for t in threads:
                if t.ident is not None:      # 没起来的线程不能 join
                    t.join(timeout=2)


async def _stream_preview(session, path, start, end, size):
    """异步包装：客户端关掉预览（或按取消）时必须立刻关掉同步生成器。

    同 _stream_body 的理由：Starlette 的 iterate_in_threadpool 不等生成器收尾，
    少了这层，取消后预览闸门会一直被占，同书签后续预览全部卡死。
    """
    gen = _preview_chunks(session, path, start, end, size)
    try:
        async for chunk in iterate_in_threadpool(gen):
            yield chunk
    finally:
        gen.close()


async def _stream_transcode(first, gen):
    """异步包装：浏览器关掉预览时立刻关掉同步生成器，远端 ffmpeg 随之退出。"""
    try:
        yield first
        async for chunk in iterate_in_threadpool(gen):
            yield chunk
    finally:
        gen.close()


@app.get("/api/bookmarks/{bid}/transcode")
def transcode_preview(bid: str, path: str, start: float = 0.0, probe: bool = False):
    """实时转码：远端 ffmpeg 降码率后边转边发（不落盘；分片 MP4，不能拖动）。

    probe=1 只做准备并回 JSON，供前端显示失败原因。
    """
    try:
        s = _get_session(bid)
        info = sfops.stat(s, path)
        if info["is_dir"]:
            raise HTTPException(400, "目录不能转码")
        if probe:
            return {"ok": True, "ffmpeg": transcode.ensure_ffmpeg(s)}
        first, gen = transcode.open_stream(s, path, start)
        return StreamingResponse(
            _stream_transcode(first, gen), media_type="video/mp4",
            headers={"Cache-Control": "no-store", "Content-Disposition": "inline"},
        )
    except HTTPException:
        raise
    except transcode.TranscodeError as e:
        raise HTTPException(500, f"转码失败: {e}")
    except FileNotFoundError:
        raise HTTPException(404, "文件不存在")
    except paramiko.SSHException as e:
        pool.dispose(bid)
        raise HTTPException(500, f"连接中断: {e}")
    except Exception as e:
        raise HTTPException(500, f"转码失败: {e}")


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
        # 音视频允许浏览器复用缓存，其余预览不缓存
        cache = "private, max-age=300" if mime.startswith(("video/", "audio/")) else "no-store"
        headers = {
            "Accept-Ranges": "bytes",
            "Cache-Control": cache,
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
                _stream_preview(s, path, 0, None, size), media_type=mime,
                headers={**headers, "Content-Length": str(size)},
            )
        start, end = rng
        return StreamingResponse(
            _stream_preview(s, path, start, end, size), status_code=206, media_type=mime,
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


@app.get("/api/settings")
def get_settings():
    """设置面板：可调项 + 当前值。"""
    return {"fields": settings.FIELDS, "values": settings.values()}


@app.put("/api/settings")
def put_settings(patch: dict):
    """保存设置：校验通过才写盘并立即生效。"""
    try:
        return {"values": settings.update(patch)}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except OSError as e:
        raise HTTPException(500, f"写入设置失败: {e}")


# SPA 回退：前端路由 /{hostname}/{path} 返回 index.html 交给前端 JS 接管。
# 必须放在最后，避免抢占 /api/* 与 /static/*（它们已由更早注册的路由匹配）。
@app.get("/{full_path:path}")
def spa_fallback(full_path: str):
    if full_path.startswith("api"):
        raise HTTPException(404, "Not Found")
    return _index_response()
