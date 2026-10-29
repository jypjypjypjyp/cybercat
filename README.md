# Cyber Cat 🐾

极简版 macOS Cyberduck —— **SFTP 文件浏览器**，webUI + Python。
极少依赖、零前端构建、启动即用。

> 参考 [Cyberduck](https://cyberduck.io/) 的书签概念，但不照抄，独立极简实现。

## 特性

- **书签管理** — 主机 / 端口 / 用户 / SSH 密钥或密码 / 默认目录，增删改查 + 连接测试
- **一键同步 `~/.ssh/config`** — 已有条目覆盖、新条目新建，免手敲主机
- **完整文件管理** — 浏览、上传、下载、新建目录、重命名、删除、多选批量操作
- **删除看得见** — 删大文件夹/批量删除时实时报进度（文件数、文件夹数、大小），随时可取消，取消后已删的不恢复；某一项失败只记一笔，其余照删
- **排序** — 按名称 / 时间 / 大小，目录永远优先
- **显示隐藏项** — 工具栏一个开关列出以 `.` 开头的文件/目录（默认显示，选择记在浏览器里）；关掉后全选与批量操作只作用于看得见的项
- **跳板机上的点文件** — SFTP 列目录省略点文件的网关（JumpServer 等）自动改用同一条连接上的 shell 补名字，属性仍取自 SFTP；shell 用不上就跳过
- **预览** — 双击文件名或点行内 👁 打开覆盖式预览：图片、视频、音频、PDF、代码（自带轻量着色）、JSON（自动美化）、纯文本（超 256 KB 截断并提示）；超过 512 MB 不预览、二进制老实说看不了，都是让你去下载
- **实时转码** — 视频卡片里点「转码播放」：远端 ffmpeg 边转边发（降到 720p、分片 MP4、不落盘），高码率、跨国线路、浏览器不支持的 mkv/avi 都能看；代价是不能拖进度，且吃服务器 CPU。服务器没装 ffmpeg 时，自动把本机缓存的可移植静态版传上去（一次性）
- **设置面板** — 顶栏「⚙ 设置」调预览并行通道、预览上限、空闲回收、下载预取、转码高度/画质/速度、默认转码播放；存在 `~/.config/cybercatoss/settings.json`，保存即生效，不用重启
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
  settings.py      运行时可改参数（设置面板的存取）
  ssh_config.py    ~/.ssh/config 解析
  sync.py          ssh config → 书签同步（覆盖/新建）
  session_pool.py  SFTP 会话池（复用 + 控制/传输常驻通道 + 预览并行读 + 空闲回收）
  sftp_ops.py      SFTP 操作封装
  preview.py       预览支撑（MIME 猜测 + Range 解析）
  transcode.py     实时转码（探测/上传 ffmpeg、拼命令、管道转字节流）
  main.py          FastAPI 路由 + 真实连接
static/            vanilla JS 前端
tests/             单元 + 端到端测试
scripts/cybercat.py  启动器（自动开浏览器）
deploy.sh         一键部署 + 注册 cybercat 命令
run.py            直接启动入口
```

## 配置

环境变量是启动默认值；界面上的「⚙ 设置」写 `~/.config/cybercatoss/settings.json` 覆盖它们，改完立即生效。

| 项 | 说明 |
|---|---|
| 书签文件 | `~/.config/cybercatoss/bookmarks.json` |
| `CYBERCATOSS_CONFIG_DIR` | 覆盖配置目录 |
| `CYBERCATOSS_IDLE_TIMEOUT` | 会话空闲回收秒数（默认 600） |
| `CYBERCAT_PORT` | 监听端口（默认 9000） |
| `CYBERCAT_PREFETCH` | SFTP 下载预取（流水线），**默认开启**；`=0` 关闭 |
| `CYBERCAT_PREFETCH_CONCURRENCY` | 预取并发数（默认 8） |
| `CYBERCAT_PREVIEW_LANES` | 预览并行读的通道数（默认 4） |
| `CYBERCAT_TRANSCODE_HEIGHT` | 实时转码输出高度上限（默认 720） |
| `CYBERCAT_TRANSCODE_CRF` | 转码画质，越大越糊也越小（默认 28） |
| `CYBERCAT_TRANSCODE_PRESET` | x264 速度档（默认 veryfast） |
| `CYBERCAT_FFMPEG_PORTABLE` | 指定本机现成的可移植 ffmpeg，跳过自动下载 |
| `CYBERCAT_TRANSCODE_DEFAULT` | 打开视频直接走转码流（`=1` 开启，默认关） |
| `CYBERCAT_PREVIEW_MAX_MB` | 预览大小上限（MB，默认 512） |

## 设计说明

- **控制 / 传输 / 预览分三条路**：每书签一条 SSH 连接——目录、增删改走控制通道（锁串行化），下载、上传走传输通道（信号量串行化），预览每条请求另开 `CYBERCAT_PREVIEW_LANES` 条独立通道、读完即关。paramiko 的 `SFTPClient` 单实例非线程安全（并发请求会死锁），故每条通道只由一个线程使用。任意一条跑多久都不挡另外两条。
- **预览并行读**：预览把请求按 256 KB 切块，分给多条通道轮流领块读、再按块序交给浏览器——把串行 32 KB 一个往返变成 4 个请求在途。实测（本机 stub，每块加 5ms 延迟）：单通道 4.1 MB/s → 4 通道 16.5 MB/s（4.0 倍）；提前关掉预览时各通道读完手头一块就退出，约 70ms 收干净，不留后台线程。服务器 `MaxSessions` 不允许多开时自动退化成更少通道。
- **实时转码不落盘**：`/transcode` 让远端 ffmpeg 读源文件、把分片 MP4（`frag_keyframe+empty_moov`）写到标准输出，本机把管道字节直接转给浏览器；关掉预览就关 SSH 通道，ffmpeg 随之退出。分片流没有总时长，所以没有进度条（接口支持 `start=` 从指定秒开始，界面暂未暴露）。服务器没装 ffmpeg 时，按 `uname` 选平台，把 `~/.cache/cybercatoss/ffmpeg/<平台>/ffmpeg` 传到家目录复用（首次 50–80 MB，之后不再传）；静态版取自 johnvansickle 的官方构建，也可用 `CYBERCAT_FFMPEG_PORTABLE` 指定自己的二进制。
- **传输之间排队、预览之间排队**：同一书签同时只跑一条下载/上传、一条预览，多选下载不会一起压服务器，但预览大文件也不会卡住列目录。
- **预览按类型走不同路子**：文本类读到 256 KB 就停（截断并提示）；图片先取进内存再当 blob 显示（上限 64 MB）；音视频和 PDF 把这条流直接交给浏览器（带 `Content-Range`，能拖动进度），不经过内存中转。超过 512 MB 的一律不预览（`PREVIEW_MAX_BYTES`）：后端在 stat 之后直接 413，前端若已知大小就连请求都不发，只在卡片里说明原因并留「下载」。
- **长列表不排屏外的行**：`.file-row` 加了 `content-visibility: auto`，屏外的行不参与排版与绘制。两万行时一次全量重排要几十秒（点排序就卡在那），跳过之后同样的操作只要零点几秒。`contain-intrinsic-size` 写的是**内容盒**高度（行高 49px − 上下 padding 16px − 下边框 1px = 32px），写成 49 或者用 `auto` 会让滚动条长度虚高 35%。
- **排序不重建 DOM**：`setSort` / `toggleSortDir` 只把现成的行按新顺序摆一遍（`applySort`，一次 `replaceChildren`），不再重新 innerHTML 整表——两万行整表是 24 万个节点，重解析一遍就是白花的几百毫秒；顺带把比较键预先算好，省掉每次比较里的 `toLowerCase`。实测（两万行，注入式测量，同一台机器）：排序 37–40 秒 → 0.2–0.5 秒，首次渲染 ~40 秒 → ~1.8 秒。勾选状态与滚动位置都保持不动。
- **列表一次给全**：`listdir_attr` 本来就要一次往返拿整个目录的属性，所以不做分页——后端一次返回全部条目，排序（名称 / 时间 / 大小）在前端对全量做。截断会让「按时间排序」只排到其中一部分，最新的文件反而不出现；代价是超大目录首包偏大。
- **外壳吸顶、一键回顶**：面包屑与工具栏包在 `.browser-head` 里，吸在顶栏正下方（`top: calc(57px * var(--u))`），列表滚到哪儿都还能改目录、换排序；工具栏右侧的「⤒ 返回顶部」把页面滚回顶端（列表要是自己滚，就滚列表）。不做平滑动画：屏外的行本来就不参与排版，跳一下就到位，而动画在后台标签页里根本不会跑。
- **静态资源带版本号**：index.html 里 `/static/style.css`、`/static/app.js` 的引用由后端重写成 `?v=<文件 mtime>`，改了文件链接就变，浏览器不会咬着缓存里的旧脚本不放（静态目录本身只带 ETag，不给浏览器强制复验的理由）。
- **不阻塞事件循环**：所有 SFTP 端点用同步 `def`，由 FastAPI 自动丢进线程池。
- **断连自愈**：SSH keepalive 保活；刷新/操作时检测连接活性，已死则自动重连；读写遇断连重试一次。

## License

[MIT](LICENSE)
