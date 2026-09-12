# Cyber Cat 🐾

极简版 macOS Cyberduck —— **SFTP 文件浏览器**，webUI + Python。
极少依赖、零前端构建、启动即用。

> 参考 [Cyberduck](https://cyberduck.io/) 的书签概念，但不照抄，独立极简实现。

## 特性

- **书签管理** — 主机 / 端口 / 用户 / SSH 密钥或密码 / 默认目录，增删改查 + 连接测试
- **一键同步 `~/.ssh/config`** — 已有条目覆盖、新条目新建，免手敲主机
- **完整文件管理** — 浏览（分页）、上传、下载、新建目录、重命名、删除、多选批量操作
- **排序** — 按名称 / 时间 / 大小，目录永远优先
- **URL 直达** — `http://127.0.0.1:9000/{主机}/{路径}` 直接打开某主机某个目录（也支持 `#/主机/路径`）
- **Fluent Design UI** — Acrylic 顶栏、层级阴影、Reveal 光效，明/暗主题切换
- **应用内缩放** — 滑杆 / `Cmd(⌘) +` `-` `0`，刷新保持
- **安全** — 密码存 macOS 钥匙串（`security` CLI），不落明文文件
- **快** — 每书签复用 SFTP 连接（免重复 SSH 握手）；同步端点跑线程池，不阻塞事件循环；列表一次往返取全属性

## 快速开始

```bash
git clone https://github.com/jypjypjypjyp/cybercat.git
cd cybercat
./deploy.sh          # uv sync 建环境 + 注册 cybercat 命令
cybercat             # 启动并自动打开浏览器
```

然后在浏览器里 `http://127.0.0.1:9000`。

> `deploy.sh --test` 会在部署后顺带跑测试。

## 手动安装

```bash
uv sync              # 按 pyproject.toml 建 .venv 并装依赖（含 dev）
.venv/bin/python run.py
```

依赖仅 4 个：`paramiko` / `fastapi` / `uvicorn` / `python-multipart`。前端零 npm 依赖。

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | 无构建 vanilla JS 单页（零 npm） |
| 后端 | FastAPI + uvicorn |
| SFTP | paramiko 5.x |
| 凭据 | macOS 钥匙串（`security` CLI） |

## 测试

```bash
.venv/bin/python -m pytest -q
```

含单元测试（书签 / 会话池 / SFTP 操作 / ssh_config 解析 / 断连重连）
与协议级端到端测试（内存 SFTP server 走完整链路）。

## 目录结构

```
app/
  config.py        路径 / 常量 / 超时 / 开关
  keychain.py      macOS 钥匙串封装
  bookmarks.py     书签 CRUD + JSON 存储
  ssh_config.py    ~/.ssh/config 解析
  sync.py          ssh config → 书签同步（覆盖/新建）
  session_pool.py  SFTP 会话池（复用 + 整连接互斥 + 空闲回收）
  sftp_ops.py      SFTP 操作封装
  main.py          FastAPI 路由 + 真实连接
static/            vanilla JS 前端
tests/             单元 + 端到端测试
scripts/cybercat.py  启动器（自动开浏览器）
deploy.sh         一键部署 + 注册 cybercat 命令
run.py            直接启动入口
```

## 配置

| 项 | 说明 |
|---|---|
| 书签文件 | `~/.config/cybercatoss/bookmarks.json` |
| `CYBERCATOSS_CONFIG_DIR` | 覆盖配置目录 |
| `CYBERCATOSS_IDLE_TIMEOUT` | 会话空闲回收秒数（默认 600） |
| `CYBERCAT_PORT` | 监听端口（默认 9000） |
| `CYBERCAT_PREFETCH` | SFTP 下载预取（流水线），**默认开启**；`=0` 关闭 |
| `CYBERCAT_PREFETCH_CONCURRENCY` | 预取并发数（默认 8） |

## 设计说明

- **单连接互斥**：paramiko 的 `SFTPClient` 单实例非线程安全（并发请求会死锁），故每书签一个连接 + 一把锁串行化所有操作。
- **不阻塞事件循环**：所有 SFTP 端点用同步 `def`，由 FastAPI 自动丢进线程池。
- **断连自愈**：SSH keepalive 保活；刷新/操作时检测连接活性，已死则自动重连；读写遇断连重试一次。

## License

[MIT](LICENSE)
