# SFTP Manager

极简版 macOS Cyberduck（SSHFS 类）SFTP 文件浏览器 —— webUI + Python，极少依赖，仅保留 SFTP 功能。

参考 [Cyberduck](https://cyberduck.io/) 的概念（书签模型），但不照抄，独立极简实现。

## 特性

- **书签管理**：host / port / 用户名 / SSH 密钥或密码 / 默认目录，增删改查 + 连接测试
- **文件管理**：浏览（分页）、上传、下载、新建目录、重命名、删除
- **缩放**：应用内 CSS 缩放（滑杆 + `Cmd/Ctrl +/–/0` 快捷键），刷新后保持
- **安全**：密码存 macOS 钥匙串（`security` CLI），不落 JSON
- **快**：每书签复用 SFTP 连接（免 SSH 握手），同步端点跑线程池（不阻塞事件循环），列表用 `listdir_attr` 一次性取全部属性再分页

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | 无构建 vanilla JS 单页（零 npm 依赖） |
| 后端 | FastAPI + uvicorn |
| SFTP | paramiko 5.0 |
| 凭据 | macOS 钥匙串（`security` CLI） |

依赖仅：`paramiko` / `fastapi` / `uvicorn` / `python-multipart`。

## 启动

```bash
# 用 uv 建环境并安装依赖
uv venv --python 3.12
uv pip install -e '.[dev]'

# 启动
source .venv/bin/activate
python run.py
# 或
uv run --project . run.py
```

浏览器打开 `http://127.0.0.1:9000`。

## 测试

```bash
source .venv/bin/activate
pytest -q
```

包含单元测试（书签 / 会话池 / SFTP 操作）+ 协议级端到端测试（内存 SFTP server 走完整链路）。

## 目录结构

```
app/
  config.py        路径/常量/超时
  keychain.py      macOS 钥匙串封装
  bookmarks.py     书签 CRUD + JSON 存储
  session_pool.py  SFTP 会话池（复用+互斥+空闲回收）
  sftp_ops.py      SFTP 操作封装
  main.py          FastAPI 路由 + 真实连接
static/            vanilla JS 前端
tests/             单元 + 端到端测试
run.py             启动入口
```

## 配置

- 书签文件：`~/.config/cybercatoss/bookmarks.json`
- 环境变量：`CYBERCATOSS_CONFIG_DIR`（配置目录）、`CYBERCATOSS_IDLE_TIMEOUT`（会话空闲回收秒数，默认 600）
