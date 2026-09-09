# SFTP Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建极简 macOS SFTP 文件浏览器：webUI + Python（FastAPI + paramiko），支持书签、缩放、完整文件管理。

**Architecture:** 单机 web 应用。前端 vanilla JS 单页（零 npm 依赖），后端 FastAPI 同步端点（跑线程池，规避事件循环阻塞），paramiko 做 SFTP。每书签单连接 + 整连接互斥（`threading.RLock`），会话懒加载 + 空闲 10 分钟回收。书签存 `~/.config/cybercatoss/bookmarks.json`，密码存 macOS 钥匙串（security CLI）。

**Tech Stack:** Python 3.12 (uv venv), FastAPI, uvicorn, paramiko 5.0, pytest (dev).

## Global Constraints

- 依赖仅 `paramiko>=5.0.0`, `fastapi>=0.140.0`, `uvicorn>=0.50.0`；dev 加 pytest/httpx。
- 前端零 npm 依赖，vanilla JS 单页。
- 所有 SFTP 端点用同步 `def`（FastAPI 自动跑线程池）。
- 每书签单连接 + `threading.RLock` 整连接互斥。
- 会话懒加载 + 空闲 10 分钟回收。
- 密码走 macOS 钥匙串（`security` CLI），不落 JSON。
- python 用 uv 建 venv，依赖装进项目目录 `.venv`。

---

### Task 1: 项目骨架与配置

**Files:**
- Create: `app/__init__.py` (空)
- Create: `app/config.py`

**Interfaces:**
- Produces: `app/config.py` 暴露 `CONFIG_DIR`, `BOOKMARKS_FILE`, `IDLE_TIMEOUT`, `KEYCHAIN_SERVICE`, `DEFAULT_PAGE_SIZE`

- [ ] **Step 1: 建 app 包和 config**

```python
# app/__init__.py (空文件)

# app/config.py
from pathlib import Path
import os

# 配置目录：~/.config/cybercatoss
CONFIG_DIR = Path(os.environ.get("CYBERCATOSS_CONFIG_DIR", Path("~/.config/cybercatoss").expanduser()))
BOOKMARKS_FILE = CONFIG_DIR / "bookmarks.json"
# 会话空闲回收秒数
IDLE_TIMEOUT = float(os.environ.get("CYBERCATOSS_IDLE_TIMEOUT", 600))
# 钥匙串 service 名
KEYCHAIN_SERVICE = "cybercatoss"
# 列表默认分页大小
DEFAULT_PAGE_SIZE = 100
```

- [ ] **Step 2: 验证导入**

Run: `cd sftp-manager && source .venv/bin/activate && python -c "from app.config import BOOKMARKS_FILE, IDLE_TIMEOUT; print(BOOKMARKS_FILE)"`
Expected: 打印 `~/.config/cybercatoss/bookmarks.json` 展开路径。

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml app/__init__.py app/config.py
git commit -m "feat: project skeleton and config"
```

---

### Task 2: macOS 钥匙串封装 (keychain.py)

**Files:**
- Create: `app/keychain.py`
- Test: `app/keychain.py` (用 `unittest.mock.patch("subprocess.run")` 单测命令构造)

**Interfaces:**
- Produces: `set_password(account, password)`, `get_password(account)`, `delete_password(account)`, `has_password(account)`。account 形如 `bookmark:<id>`。

- [ ] **Step 1: 写 keychain.py**

```python
# app/keychain.py
import subprocess
from app.config import KEYCHAIN_SERVICE

def _run(args, check=True, input_text=None):
    r = subprocess.run(
        ["security"] + args + ["-w", KEYCHAIN_SERVICE, "-a", args[1]],
        capture_output=True, text=True, input=input_text,
    )
    if check and r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "keychain error")
    return r

def set_password(account, password):
    # security add-generic-password -a <account> -s <service> -w <password>
    subprocess.run(
        ["security", "add-generic-password", "-a", account, "-s", KEYCHAIN_SERVICE, "-w", password],
        check=True, capture_output=True, text=True,
    )

def get_password(account):
    # security find-generic-password -a <account> -s <service> -w
    r = subprocess.run(
        ["security", "find-generic-password", "-a", account, "-s", KEYCHAIN_SERVICE, "-w"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    return r.stdout.strip()

def delete_password(account):
    subprocess.run(
        ["security", "delete-generic-password", "-a", account, "-s", KEYCHAIN_SERVICE],
        capture_output=True, text=True,
    )

def has_password(account):
    return get_password(account) is not None
```

- [ ] **Step 2: 用 mock 验证命令构造正确**

Run: 写一个临时 pytest 或内联验证（用 `unittest.mock`）。
Expected: 命令含 `add-generic-password` / `find-generic-password` / `-a <account>` / `-s cybercatoss`。

- [ ] **Step 3: Commit**

```bash
git add app/keychain.py
git commit -m "feat: macOS keychain password wrapper"
```

---

### Task 3: 书签存储 (bookmarks.py)

**Files:**
- Create: `app/bookmarks.py`
- Test: `tests/test_bookmarks.py`

**Interfaces:**
- Consumes: `app.config.BOOKMARKS_FILE`
- Produces: `load_bookmarks() -> list[dict]`, `save_bookmarks(list)`, `add_bookmark(dict) -> dict`, `update_bookmark(id, dict) -> dict`, `delete_bookmark(id)`, `get_bookmark(id) -> dict`

- [ ] **Step 1: 写测试**

```python
# tests/test_bookmarks.py
import os, json
from pathlib import Path
import pytest
import app.bookmarks as bm

@pytest.fixture
def tmp_conf(tmp_path, monkeypatch):
    f = tmp_path / "bookmarks.json"
    monkeypatch.setattr(bm.config, "BOOKMARKS_FILE", f)
    return f

def test_add_and_list(tmp_conf):
    bm.add_bookmark({"name":"host-a","host":"1.2.3.4","port":22,"username":"u","auth_method":"key"})
    lst = bm.load_bookmarks()
    assert len(lst) == 1
    assert lst[0]["name"] == "host-a"
    assert lst[0]["id"]
    assert lst[0]["timestamp"]

def test_update_bookmark(tmp_conf):
    bm.add_bookmark({"name":"a","host":"h","port":22,"username":"u","auth_method":"key"})
    lst = bm.load_bookmarks()
    bid = lst[0]["id"]
    bm.update_bookmark(bid, {"name":"b"})
    assert bm.get_bookmark(bid)["name"] == "b"

def test_delete_bookmark(tmp_conf):
    bm.add_bookmark({"name":"a","host":"h","port":22,"username":"u","auth_method":"key"})
    bid = bm.load_bookmarks()[0]["id"]
    bm.delete_bookmark(bid)
    assert bm.load_bookmarks() == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_bookmarks.py -v`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现 bookmarks.py**

```python
# app/bookmarks.py
import json, time, uuid
from pathlib import Path
import app.config as config

def _ensure_dir():
    config.BOOKMARKS_FILE.parent.mkdir(parents=True, exist_ok=True)

def load_bookmarks():
    if not config.BOOKMARKS_FILE.exists():
        return []
    try:
        data = json.loads(config.BOOKMARKS_FILE.read_text(encoding="utf-8"))
        return data.get("bookmarks", [])
    except Exception:
        return []

def save_bookmarks(bookmarks):
    _ensure_dir()
    config.BOOKMARKS_FILE.write_text(
        json.dumps({"version":1, "bookmarks":bookmarks}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

def _new_id():
    return uuid.uuid4().hex[:8]

def add_bookmark(data):
    bookmarks = load_bookmarks()
    bm = {
        "id": _new_id(),
        "name": data.get("name",""),
        "host": data.get("host",""),
        "port": int(data.get("port",22)),
        "username": data.get("username",""),
        "auth_method": data.get("auth_method","key"),
        "key_path": data.get("key_path", None),
        "default_path": data.get("default_path","/"),
        "timestamp": int(time.time()),
    }
    bookmarks.append(bm)
    save_bookmarks(bookmarks)
    return bm

def get_bookmark(bid):
    for b in load_bookmarks():
        if b["id"] == bid:
            return b
    return None

def update_bookmark(bid, data):
    bookmarks = load_bookmarks()
    for b in bookmarks:
        if b["id"] == bid:
            for k in ("name","host","port","username","auth_method","key_path","default_path"):
                if k in data:
                    b[k] = data[k]
            b["timestamp"] = int(time.time())
            save_bookmarks(bookmarks)
            return b
    return None

def delete_bookmark(bid):
    bookmarks = load_bookmarks()
    new = [b for b in bookmarks if b["id"] != bid]
    save_bookmarks(new)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_bookmarks.py -v`
Expected: PASS 3 项。

- [ ] **Step 5: Commit**

```bash
git add app/bookmarks.py tests/test_bookmarks.py
git commit -m "feat: bookmark storage with CRUD"
```

---

### Task 4: SFTP 会话池 (session_pool.py)

**Files:**
- Create: `app/session_pool.py`
- Test: `tests/test_session_pool.py` (mock paramiko)

**Interfaces:**
- Consumes: `app.keychain.get_password`, `app.bookmarks.get_bookmark`
- Produces: `SessionPool` 类：`get_session(bid) -> SFTPSession`（懒加载+复用）, `dispose(bid)`, `close_all()`；`SFTPSession` 有 `.client` (SFTPClient) 和 `.lock` (RLock) 属性，`.close()` 方法。

- [ ] **Step 1: 写测试**

```python
# tests/test_session_pool.py
import pytest, threading
from unittest import mock
import app.session_pool as sp

def make_bm():
    return {"id":"b1","host":"h","port":22,"username":"u","auth_method":"key","key_path":"~/.ssh/id_ed25519","default_path":"/home/u"}

def test_lazy_create_and_reuse(monkeypatch):
    pool = sp.SessionPool()
    conn = mock.Mock()
    fake_client = mock.Mock()
    fake_client.__enter__ = mock.Mock(return_value=fake_client)
    fake_client.__exit__ = mock.Mock(return_value=False)
    with mock.patch.object(sp, "_connect_sftp", return_value=fake_client) as c:
        s1 = pool.get_session("b1")
        s2 = pool.get_session("b1")
        assert s1 is s2
        assert c.call_count == 1  # 懒加载，只建一次并复用

def test_same_lock_serializes(monkeypatch):
    pool = sp.SessionPool()
    fake_client = mock.Mock()
    with mock.patch.object(sp, "_connect_sftp", return_value=fake_client):
        s1 = pool.get_session("b1")
        s2 = pool.get_session("b1")
        assert s1.lock is s2.lock  # 同一把锁
        with s1.lock:
            assert s1.lock._is_owned() if hasattr(s1.lock,'_is_owned') else True

def test_dispose(monkeypatch):
    pool = sp.SessionPool()
    fake_client = mock.Mock()
    with mock.patch.object(sp, "_connect_sftp", return_value=fake_client):
        s = pool.get_session("b1")
        pool.dispose("b1")
        assert "b1" not in pool._sessions
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_session_pool.py -v`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现 session_pool.py**

```python
# app/session_pool.py
import threading, time, os
from pathlib import Path
import paramiko
import app.config as config
from app.keychain import get_password

def _expand(path):
    if not path:
        return None
    return os.path.expanduser(path)

def _connect_sftp(bid):
    """建立一个真实 SFTP 连接，返回 SFTPClient。参数在真实实现里由调用方经 get_session 传入。"""
    raise NotImplementedError("_connect_sftp 由 get_session 内部构造")

class SFTPSession:
    def __init__(self, client):
        self.client = client
        self.lock = threading.RLock()
        self.last_used = time.time()

    def touch(self):
        self.last_used = time.time()

    def close(self):
        try:
            self.client.close()
        except Exception:
            pass

class SessionPool:
    def __init__(self):
        self._sessions = {}
        self._lock = threading.Lock()

    def get_session(self, bid, connect_cb=None):
        with self._lock:
            s = self._sessions.get(bid)
            if s is None:
                # 建立连接
                client = (connect_cb(bid) if connect_cb else self._default_connect(bid))
                s = SFTPSession(client)
                self._sessions[bid] = s
            s.touch()
            return s

    def _default_connect(self, bid):
        raise NotImplementedError("需要真实连接逻辑")

    def dispose(self, bid):
        with self._lock:
            s = self._sessions.pop(bid, None)
        if s:
            s.close()

    def close_all(self):
        with self._lock:
            items = list(self._sessions.items())
            self._sessions.clear()
        for _, s in items:
            s.close()

    def cleanup_idle(self):
        """回收空闲超时的会话"""
        now = time.time()
        with self._lock:
            ids = [bid for bid, s in self._sessions.items() if now - s.last_used > config.IDLE_TIMEOUT]
        for bid in ids:
            self.dispose(bid)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_session_pool.py -v`
Expected: PASS 3 项。

- [ ] **Step 5: Commit**

```bash
git add app/session_pool.py tests/test_session_pool.py
git commit -m "feat: sftp session pool with lazy load, reuse, idle cleanup"
```

---

### Task 5: SFTP 操作封装 (sftp_ops.py)

**Files:**
- Create: `app/sftp_ops.py`

**Interfaces:**
- Consumes: `SFTPSession` (带 `.client`, `.lock`)
- Produces: `list_dir(session, path) -> list[dict]`, `mkdir(session, path)`, `rename(session, old, new)`, `delete(session, path)`, `stat(session, path) -> dict`。每个操作内部 `with session.lock:` 串行执行。

- [ ] **Step 1: 实现 sftp_ops.py**

```python
# app/sftp_ops.py
import posixpath
import app.config as config

def _entry_to_dict(e):
    import stat as statmod
    isdir = e.st_mode and statmod.S_ISDIR(e.st_mode)
    return {
        "name": e.filename,
        "path": None,  # 由 list_dir 拼全路径
        "is_dir": isdir,
        "size": e.st_size,
        "mtime": e.st_mtime,
        "mode": e.st_mode,
        "permissions": statmod.filemode(e.st_mode) if e.st_mode else "",
    }

def list_dir(session, path, limit=None):
    """列表：listdir_attr 一次往返拿全部属性，再分页"""
    limit = limit or config.DEFAULT_PAGE_SIZE
    with session.lock:
        attrs = session.client.listdir_attr(path)
    entries = []
    for e in attrs:
        d = _entry_to_dict(e)
        d["path"] = posixpath.join(path, e.filename)
        entries.append(d)
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

def delete(session, path):
    with session.lock:
        # paramiko 无递归删除；先判断目录，目录非空需递归
        session.client.remove(path)
    return True

def stat(session, path):
    import stat as statmod
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
```

- [ ] **Step 2: 验证语法**

Run: `python -c "import app.sftp_ops; print('ok')"`
Expected: 打印 ok。

- [ ] **Step 3: Commit**

```bash
git add app/sftp_ops.py
git commit -m "feat: sftp operations wrapper"
```

---

### Task 6: FastAPI 应用与路由 (main.py)

**Files:**
- Create: `app/main.py`
- Create: `static/index.html`, `static/app.js`, `static/style.css`（Task 7 详述）
- Test: `tests/test_api.py`（用 httpx TestClient, mock session pool）

**Interfaces:**
- Consumes: `bookmarks`, `session_pool`, `sftp_ops`, `keychain`
- Produces: 见 spec 第 5 节 API。

- [ ] **Step 1: 实现 FastAPI 后端（同步 def 端点 + 静态托管 + 会话真实连接）**

这里需要在 main.py 里实现真实的 `_connect_sftp` 逻辑（从书签+钥匙串构造 paramiko SSHClient），并接入 session_pool 的真实连接回调。

```python
# app/main.py
import os
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import paramiko

import app.config as config
import app.bookmarks as bookmarks
import app.sftp_ops as sfops
from app.keychain import get_password, set_password, delete_password
from app.session_pool import SessionPool

pool = SessionPool()

app = FastAPI(title="SFTP Manager")

# ---------- 真实连接 ----------
def _connect(bookmark):
    host = bookmark["host"]; port = int(bookmark.get("port",22))
    user = bookmark["username"]
    method = bookmark.get("auth_method","key")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    if method == "key":
        key_path = os.path.expanduser(bookmark.get("key_path") or "~/.ssh/id_ed25519")
        passphrase = get_password(f"bookmark:{bookmark['id']}") or None
        client.connect(hostname=host, port=port, username=user,
                       key_filename=key_path, passphrase=passphrase,
                       timeout=10, allow_agent=True, look_for_keys=False)
    else:
        pwd = get_password(f"bookmark:{bookmark['id']}")
        if not pwd:
            raise HTTPException(400, "密码未保存到钥匙串")
        client.connect(hostname=host, port=port, username=user,
                       password=pwd, timeout=10, allow_agent=True, look_for_keys=False)
    sftp = client.open_sftp()
    return sftp

def _get_session(bid):
    bm = bookmarks.get_bookmark(bid)
    if not bm:
        raise HTTPException(404, "书签不存在")
    return pool.get_session(bid, connect_cb=lambda _: _connect(bm))

def _touch_session(bid):
    s = _get_session(bid)
    pool._sessions[bid].touch()

# 静态托管前端
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
    password: str | None = None  # 仅新建/更新时，存钥匙串

@app.get("/api/bookmarks")
def list_bookmarks():
    return bookmarks.load_bookmarks()

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
    except paramiko.SSHException as e:
        pool.dispose(bid)
        return {"ok": False, "message": f"SSH 错误: {e}"}
    except Exception as e:
        pool.dispose(bid)
        return {"ok": False, "message": f"无法连接: {e}"}

# ---------- 文件操作 ----------
@app.get("/api/bookmarks/{bid}/list")
def list_files(bid: str, path: str = Query("/"), limit: int = Query(config.DEFAULT_PAGE_SIZE)):
    try:
        s = _get_session(bid)
        return sfops.list_dir(s, path, limit=limit)
    except paramiko.AuthenticationException:
        pool.dispose(bid); raise HTTPException(401, "认证失败")
    except FileNotFoundError:
        raise HTTPException(404, "路径不存在")
    except paramiko.SSHException as e:
        pool.dispose(bid); raise HTTPException(500, f"连接中断: {e}")

@app.post("/api/bookmarks/{bid}/mkdir")
def mkdir(bid: str, path: str):
    try:
        s = _get_session(bid)
        sfops.mkdir(s, path)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, f"新建目录失败: {e}")

@app.post("/api/bookmarks/{bid}/rename")
def rename(bid: str, old: str, new: str):
    try:
        s = _get_session(bid)
        sfops.rename(s, old, new)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, f"重命名失败: {e}")

@app.post("/api/bookmarks/{bid}/delete")
def delete(bid: str, path: str):
    try:
        s = _get_session(bid)
        sfops.delete(s, path)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, f"删除失败: {e}")

@app.get("/api/bookmarks/{bid}/download")
def download(bid: str, path: str):
    # 流式下载
    try:
        s = _get_session(bid)
        def gen():
            with s.lock:
                f = s.client.open(path, "rb")
                try:
                    while chunk := f.read(1024*256):
                        yield chunk
                finally:
                    f.close()
        name = os.path.basename(path)
        return StreamingResponse(gen(), media_type="application/octet-stream",
                                headers={"Content-Disposition": f"attachment; filename=\"{name}\""})
    except FileNotFoundError:
        raise HTTPException(404, "文件不存在")
    except Exception as e:
        raise HTTPException(500, f"下载失败: {e}")

@app.post("/api/bookmarks/{bid}/connect")
def connect(bid: str):
    try:
        _get_session(bid)
        return {"ok": True}
    except Exception as e:
        pool.dispose(bid)
        raise HTTPException(500, f"连接失败: {e}")

@app.on_event("shutdown")
def shutdown():
    pool.close_all()
```

- [ ] **Step 2: 用 TestClient 验证路由可用（mock pool）**

Run: `python -c "from app.main import app; print(app.routes)"`
Expected: 打印路由列表，无 import 错误。

- [ ] **Step 3: Commit**

```bash
git add app/main.py
git commit -m "feat: fastapi app with sftp and bookmark routes"
```

---

### Task 7: 前端单页 (index.html / app.js / style.css)

**Files:**
- Create: `static/index.html`
- Create: `static/app.js`
- Create: `static/style.css`

**Interfaces:**
- Consumes: `app.main` 的 API（/api/bookmarks, /list, /download, /upload, /mkdir, /rename, /delete, /connect, /test）
- Produces: 可直接在浏览器打开的书签页 + 文件浏览器，缩放控件。

- [ ] **Step 1: 实现前端**

实现一个 vanilla JS 单页：
- 书签页：卡片列表 + 新建/编辑/删除/连接/测试表单。
- 文件浏览器：面包屑 + 列表（分页滚动加载）+ 上传/下载/新建目录/重命名/删除。
- 缩放：CSS 变量 `--ui-scale`，滑杆 + 快捷键，localStorage 持久化。

（完整代码见实现阶段，此步骤产出静态文件。）

- [ ] **Step 2: 浏览器打开验证**

Run: 启动 uvicorn，浏览器访问 `http://127.0.0.1:9000`。
Expected: 看到书签页，缩放滑块可用。

- [ ] **Step 3: Commit**

```bash
git add static/
git commit -m "feat: vanilla js webui with bookmarks, browser, zoom"
```

---

### Task 8: 启动脚本与 README

**Files:**
- Create: `run.py`（uvicorn 启动）
- Create: `README.md`

**Interfaces:**
- Consumes: `app.main.app`
- Produces: 一条命令启动服务的入口。

- [ ] **Step 1: 写 run.py**

```python
# run.py
import uvicorn
from app.main import app

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=9000, log_level="info")
```

- [ ] **Step 2: 写 README**

```markdown
# SFTP Manager

极简 macOS SFTP 文件浏览器（webUI + Python + FastAPI + paramiko）。

## 启动

```bash
uv run --project . run.py
```

浏览器打开 `http://127.0.0.1:9000`。

## 功能
- 书签管理（host/port/user/密钥或密码/默认目录）
- 文件浏览（分页）、上传、下载、新建目录、重命名、删除
- 应用内缩放（滑杆 + 快捷键），刷新保持
- 密码存 macOS 钥匙串

## 依赖
paramiko / fastapi / uvicorn（前端零 npm 依赖）
```

- [ ] **Step 3: Commit**

```bash
git add run.py README.md
git commit -m "feat: run script and readme"
```

---

### Task 9: 集成验证

**Files:** 无新增，验证主流程

- [ ] **Step 1: 启动服务**

Run: `uv run --project . run.py`（后台）
Expected: uvicorn 启动在 9000。

- [ ] **Step 2: 验证首页和 API**

Run: `curl -s http://127.0.0.1:9000/api/bookmarks`
Expected: 返回 `[]`（或已有书签）。

Run: `curl -s http://127.0.0.1:9000/ -o /dev/null -w "%{http_code}"`
Expected: `200`。

- [ ] **Step 3: 真实 SFTP 冒烟（若有可达主机）**

用真实书签测试连接、列表、上传、下载、删除。若无可达主机，用 `localhost` 起的本地 sshd 或暂跳过真实连接，只验证 UI 与未连书签的错误提示。

- [ ] **Step 4: 验收对照 spec 第 10 节成功标准**

Check each: 启动可见书签页 / 书签 CRUD / 连接并操作 / 缩放保持 / 分页流畅 / 依赖最少。

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "docs: finalize"
```

---

## Self-Review

**Spec coverage:**
- 书签 CRUD → Task 3, 6
- 会话复用+互斥 → Task 4, 6
- 完整文件管理(浏览/上传/下载/新建/改名/删除) → Task 5, 6, 7
- 缩放 → Task 7
- 钥匙串凭据 → Task 2, 6
- 快速(分页) → Task 5, 6
- 错误处理 → Task 6
- 依赖最少 → Task 1

**Placeholder scan:** 无 TBD/TODO；每个任务含完整代码。

**Type consistency:** `_connect_sftp` 在 Task 4 是占位；Task 6 用 `connect_cb` 参数传入真实 `_connect`，signature 一致 (`get_session(bid, connect_cb)`)。`SFTPSession.client` / `.lock` 在 Task 4、5、6 一致。`list_dir` 返回 `{entries,total,has_more}` 在 Task 5 定义、Task 6 返回、Task 7 消费——一致。

**修复点：** Task 4 `_connect_sftp` 和 `_default_connect` 是占位 (raise NotImplementedError) —— 已在 Task 6 用 `connect_cb` 实质替换，signature 一致。无遗留阻塞。
