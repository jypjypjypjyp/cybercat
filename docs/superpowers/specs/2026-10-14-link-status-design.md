# 顶栏链接状态指示器 · 设计

日期：2026-10-14
状态：已实现（本文件与代码同步）
需求原话：「在 UI 上加上链接状态，用来标识【连接、空闲、回收】」，并在确认时补一句「不用写 mm:ss 后回收」。

## 1. 目标

在顶栏（Logo 与「书签」按钮之间）显示当前正在浏览的那台主机的会话状态，三态：`已连接` / `空闲` / `已回收`。只在文件浏览器视图显示，书签页隐藏。

## 2. 判据（单一规则）

| 状态 | 判据（`SessionPool.link_state`） | 显示 |
|---|---|---|
| `connected` | 会话在池中，且 `now - last_used < config.ACTIVE_WINDOW_SECONDS` | 🟢 已连接 |
| `idle` | 会话在池中，但已超过该窗口没用过 | 🟡 空闲 |
| `reclaimed` | 会话不在池中（被空闲回收，或还没连过） | ⚪ 已回收 |

`ACTIVE_WINDOW_SECONDS = 60.0`：不设这个窗口的话，任何一次操作结束的瞬间就会显示「空闲」，「已连接」永远看不见。

## 3. 数据流

前端每 2 秒 `GET /api/bookmarks/{bid}/session` → `{"state": "connected"|"idle"|"reclaimed"}`（只返回状态，不含剩余秒数）；书签不存在返回 404。进入文件浏览器时立即问一次，不空等 2 秒；回到书签页停止轮询并隐藏指示器。

## 4. 已知取舍（明确不做）

- **显示的是应用的认知，不是实时探活**：底层 SSH 连接若已被静默切断（拔网线等），顶栏仍显示「已连接」，直到下一次操作触发 `_session_alive()` 检测并重连。要做到轮询即探活，等于每 2 秒发起一次真实探测，属于为边缘情况堆保险，不做。
- **不做倒计时**：需求方明确不要「mm:ss 后回收」，端点因此也不返回剩余秒数。
- **不做手动「立即回收 / 重连」按钮**：状态只读。

## 5. 改动清单

- `app/config.py`：新增 `ACTIVE_WINDOW_SECONDS`。
- `app/session_pool.py`：新增只读方法 `link_state(bid)`。
- `app/main.py`：新增 `GET /api/bookmarks/{bid}/session`。
- `static/index.html`：顶栏加 `#link-state` 容器。
- `static/app.js`：`startLinkPolling` / `stopLinkPolling` / `pollLinkState` / `paintLinkState`，2 秒间隔，切换主机用代次号作废在途响应。
- `static/style.css`：`.link-state` 与三态圆点（`[data-state=…]::before`）。
- `tests/test_session_pool.py`、`tests/test_api.py`：三态判据与端点用例。

## 6. 验收

1. `.venv/bin/python -m pytest -q` 全绿。
2. 真机：真 uvicorn + 真 SFTP stub，IDLE_TIMEOUT 调小，肉眼确认三个状态依次出现（刚连接 → 闲置满 1 分钟 → 被回收），书签页不显示指示器。
