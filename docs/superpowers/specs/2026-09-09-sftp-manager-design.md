# SFTP Manager 设计文档

日期：2026-09-09
状态：待实现
参考：Cyberduck（`../cyberduck/`，仅参考概念，不复制代码）

## 1. 目标

开发一款**极简版 macOS Cyberduck（SSHFS 类）SFTP 管理工具**：webUI + Python，极少依赖，仅保留 SFTP 功能，支持书签、可调节缩放、速度快。

## 2. 已确认需求

| 项 | 决定 |
|---|---|
| 核心形态 | SFTP 文件浏览器（非 SSHFS 挂载管理器，非两者混合） |
| 功能范围 | 完整文件管理：浏览 / 上传 / 下载 / 新建目录 / 重命名 / 删除 |
| 后端依赖 | paramiko + FastAPI + uvicorn |
| 凭据存储 | macOS 钥匙串（`security` CLI，零额外 python 依赖） |
| 前端 | 无构建 vanilla JS 单页，零 npm 依赖，由 FastAPI 静态托管 |
| 缩放 | 应用内 CSS 缩放 + 快捷键（非纯浏览器缩放），持久化 localStorage |
| 环境 | 主人 macOS；python 用 uv 建 venv，依赖装进项目目录 `.venv` |

## 3. 架构

单机 web 应用：浏览器(前端) ← HTTP/SSE → FastAPI(后端) ← paramiko → 远程 SFTP 服务器。

```
static/  (vanilla JS 单页)         app/  (FastAPI)
┌──────────────┐  HTTP/SSE  ┌─────────────────────┐  paramiko  ┌──────────┐
│ 书签页/浏览器  │ <────────> │ 路由·会话池·验证·书签  │ <────────> │ SFTP 服务器│
└──────────────┘            └─────────────────────┘            └──────────┘
                                      │
                                      ├─ keychain.py  (macOS security CLI)
                                      ├─ bookmarks.py (JSON 存储)
                                      └─ session_pool.py (连接复用+互斥)
```

## 4. 关键设计决策

### 4.1 paramiko 线程安全与并发模型（重要）

paramiko `SFTPClient` **单实例非线程安全**——多线程并发请求（包括并发读）会导致 request/response 交错而死锁/冻住（实证：#1836、#718）。

**修正方案：每书签单连接 + 整连接互斥。**
- 每个书签一个 `SFTPClient` 实例，配一个 `threading.RLock`，**所有 SFTP 操作串行化**（不分读写）。
- 「快」的来源是**复用连接免 SSH 握手**（列表秒回），而非读并发。
- 性能优化点：列表用 `listdir_attr` **一次往返拿全部属性**（避免逐文件 stat）；大目录**分页**返回、前端增量渲染。

### 4.2 FastAPI 执行模型对齐（重要）

paramiko 是**阻塞式 I/O**。若在 `async def` 端点里直接调用，会阻塞整个事件循环（所有请求卡死）。

**对策：所有 SFTP 端点用同步 `def`**，FastAPI 自动将它们跑进线程池（`run_in_threadpool`），天然规避事件循环阻塞。

锁边界：`with session.lock:` 精确包裹**单个交互单元**（一次列表 / 一次上传 / 一次下载），操作结束即释放，**绝不跨 await**（同步端点本身无 await）。

### 4.3 SFTP 会话生命周期

- **懒加载**：首次对某书签发起操作时才创建连接（SSH 握手）。
- **复用**：后续操作复用同一连接。
- **空闲回收**：空闲超过 10 分钟自动关闭连接；再次操作自动重建。
- **失败清理**：连接失败 / 认证失败 / 连接中断时清除会话缓存，返回明确错误，下次操作重建。
- **重启**：后端重启全部与会话断开（无持久会话）。

## 5. API 接口

```
# 书签
GET    /api/bookmarks                    列出书签
POST   /api/bookmarks                    新建书签
PUT    /api/bookmarks/{id}               更新书签
DELETE /api/bookmarks/{id}               删除书签
POST   /api/bookmarks/{id}/test          测试连接（区分 认证失败/无法连接/成功）

# 文件操作（均按书签 id 取会话）
GET    /api/bookmarks/{id}/list?path=/       列表（分页）
GET    /api/bookmarks/{id}/download?path=    下载文件（流式）
POST   /api/bookmarks/{id}/upload?path=      上传文件（流式，multipart）
POST   /api/bookmarks/{id}/mkdir?path=       新建目录
POST   /api/bookmarks/{id}/rename?path=      重命名（old->new）
POST   /api/bookmarks/{id}/delete?path=      删除
POST   /api/bookmarks/{id}/connect           建立/复用会话（连接页用）
```

## 6. 书签数据模型

存储位置：`~/.config/cybercatoss/bookmarks.json`

```json
{
  "version": 1,
  "bookmarks": [
    {
      "id": "a1b2c3d4",
      "name": "host-a 数据机",
      "host": "192.168.1.10",
      "port": 22,
      "username": "alice",
      "auth_method": "key",
      "key_path": "~/.ssh/id_ed25519",
      "default_path": "/home/alice",
      "timestamp": 1750000000
    }
  ]
}
```

- 密码不落 JSON；连接时从钥匙串读取（`cybercatoss:<bookmark_id>`）。
- 私钥用文件路径，passphrase 从钥匙串读。
- `key_path` 用 `Path.expanduser()` 展开，不信任字面量 `~`。

## 7. 前端视图与缩放

### 7.1 视图
1. **书签页**（启动页）：书签卡片列表，可新建/编辑/删除/连接/测试。
2. **文件浏览器**：面包屑导航 + 文件列表；上传/下载/新建目录/重命名/删除；双击文件夹进入，点文件下载。
3. **轻量详情**：选中文件看 size/mtime/permission。

### 7.2 缩放
- 应用内 CSS 缩放，用 CSS 变量 `--ui-scale` 控制元素、行高、图标、字体的统一缩放。
- 滑杆 + 快捷键（`Cmd/Ctrl +` / `-` / `0`）调节；持久化到 localStorage。

### 7.3 快速体验
- 大目录分页 + 增量渲染（滚动加载）。
- 当前目录读结果做短 TTL 内存缓存（避免来回切目录重复请求），但不过度缓存（远程可能被外部改）。

## 8. 错误处理（友好）

| 场景 | 返回 |
|---|---|
| 连接失败（超时/不可达） | `无法连接 host:port` |
| 认证失败（密钥/密码错） | `认证失败，请检查密钥或密码` |
| 权限不足 | `权限不足` + 异常详情 |
| 连接中断 | 清会话缓存，重连前提示 |

统一走 FastAPI `HTTPException` + 结构化 `detail`，前端 toast 提示，不堆 raw traceback。

## 9. 依赖与版本

已验证可在 macOS 安装导入（uv venv）：

```
paramiko 5.0.0
fastapi  0.141.1
uvicorn  0.52.4
```

注意：paramiko 5.0 已移除 RSA SHA1 支持（老服务器 `ssh-rsa` 主机密钥可能不兼容）；本项目连标准 OpenSSH server（通常 ed25519/ecdsa），不受影响。`AutoAddPolicy` 路径在 5.0 保持不变。

## 10. 成功标准

1. 启动 `uvicorn` 后浏览器打开 `http://127.0.0.1:%d` 可见书签页。
2. 能新建/编辑/删除书签，测试连接。
3. 能连接真实 SFTP，浏览目录、上传、下载、新建目录、重命名、删除。
4. 缩放滑杆/快捷键生效且刷新后保持。
5. 大目录列表分页流畅，不卡住其他请求（验证事件循环未被阻塞）。
6. 依赖仅 paramiko/fastapi/uvicorn；前端零 npm 依赖。

## 11. ~/.ssh/config 同步（追加功能）

- 解析 `~/.ssh/config`（跳过通配 `Host *`），映射为书签：name=Host别名, host=HostName, port, username=User, auth_method=key(有 IdentityFile), key_path=IdentityFile。
- **匹配键（已确认）：按 Host 别名匹配书签 name**——存在则覆盖 host/port/username/auth_method/key_path（保留 id 与钥匙串密码），没有则新建。
- 同步不改动钥匙串密码。
- API：`POST /api/sync-ssh-config`，返回 `{ok, created, updated, skipped}`。
