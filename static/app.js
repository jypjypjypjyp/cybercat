// SFTP Manager —— vanilla JS 前端逻辑
(function () {
  "use strict";

  // ---------- 状态 ----------
  const state = {
    view: "books",          // books | browser
    bookmarks: [],
    current: null,           // 当前书签
    path: "/",
    entries: [],
    total: 0,
    selected: null,
    loading: false,
    scale: 1,
    sortKey: "name",   // name | mtime | size
    sortDir: "asc",    // asc | desc
    showHidden: true,  // 是否列出以 . 开头的隐藏项（记住上次选择）
    transcodeDefault: false,  // 打开视频默认走转码流
    videoUrl: "",             // 当前视频的直连地址
    theme: "light",
    checkAll: false,
    selectedSet: new Set(),  // 多选路径集合
    deleteBusy: false,       // 删除进行中（只能点「取消」，避免误关弹层）
    deleteAbort: null,       // 删除流的取消句柄
  };

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  // ---------- 工具 ----------
  async function api(method, url, body, isForm) {
    const opts = { method };
    if (body !== undefined) {
      opts.body = isForm ? body : JSON.stringify(body);
      if (!isForm) opts.headers = { "Content-Type": "application/json" };
    }
    const res = await fetch(url, opts);
    if (res.status === 204) return null;
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `请求失败 (${res.status})`);
    return data;
  }

  function toast(msg, type) {
    const t = $("#toast");
    t.textContent = msg;
    t.className = `toast ${type || ""}`;
    t.classList.remove("hidden");
    setTimeout(() => t.classList.add("hidden"), 2500);
  }

  function fmtSize(n) {
    if (n == null) return "-";
    if (n < 1024) return n + " B";
    const units = ["KB", "MB", "GB", "TB"];
    let v = n / 1024, i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return v.toFixed(1) + " " + units[i];
  }

  function fmtTime(ts) {
    if (!ts) return "-";
    const d = new Date(ts * 1000);
    return d.toLocaleString();
  }

  function saveScale() {
    localStorage.setItem("sfpm_scale", String(state.scale));
  }

  function applyScale() {
    document.documentElement.style.setProperty("--ui-scale", String(state.scale));
    $("#zoom-label").textContent = Math.round(state.scale * 100) + "%";
  }

  function initScale() {
    const saved = parseFloat(localStorage.getItem("sfpm_scale"));
    state.scale = isFinite(saved) && saved > 0.5 && saved < 2.5 ? saved : 1;
    applyScale();
  }

  // ---------- 顶栏链接状态 ----------
  // 每 2 秒问一次后端，只画状态，不算剩余时间。
  const LINK_POLL_MS = 2000;
  const LINK_LABEL = { connected: "已连接", idle: "空闲", reclaimed: "已回收" };

  let linkTimer = null;
  let linkSeq = 0;   // 轮询代次：换主机/回书签页时作废在途响应

  function paintLinkState(s) {
    const el = $("#link-state");
    if (!el) return;
    el.dataset.state = s;
    el.classList.remove("hidden");
    $("#link-state-text").textContent = LINK_LABEL[s] || "未知";
  }

  function stopLinkPolling() {
    linkSeq++;
    if (linkTimer) { clearInterval(linkTimer); linkTimer = null; }
    const el = $("#link-state");
    if (el) el.classList.add("hidden");
  }

  async function pollLinkState() {
    const bm = state.current;
    if (state.view !== "browser" || !bm) return;
    const token = linkSeq;
    try {
      const r = await api("GET", `/api/bookmarks/${bm.id}/session`);
      if (token === linkSeq) paintLinkState(r.state);
    } catch (e) { /* 轮询失败静默：下一轮再试 */ }
  }

  function startLinkPolling() {
    linkSeq++;
    pollLinkState();                       // 立刻问一次，不空等 2 秒
    if (linkTimer) clearInterval(linkTimer);
    linkTimer = setInterval(pollLinkState, LINK_POLL_MS);
  }

  // ---------- 视图切换 ----------
  function showBooks() {
    state.view = "books";
    state.current = null;
    $("#view-books").classList.remove("hidden");
    $("#view-browser").classList.add("hidden");
    $("#btn-home").classList.add("active");
    stopLinkPolling();
    renderBooks();
  }

  function showBrowser(bm, initialPath) {
    state.view = "browser";
    state.current = bm;
    state.path = initialPath || bm.default_path || "/";
    state.entries = [];
    state.selected = null;
    $("#view-books").classList.add("hidden");
    $("#view-browser").classList.remove("hidden");
    $("#btn-home").classList.remove("active");
    loadDir();
    startLinkPolling();
  }

  // ---------- 书签页 ----------
  async function loadBookmarks() {
    try {
      state.bookmarks = await api("GET", "/api/bookmarks");
      renderBooks();
    } catch (e) {
      toast(e.message, "err");
    }
  }

  function renderBooks() {
    const v = $("#view-books");
    const header = `
      <div class="books-header">
        <div class="books-title">我的书签<small>从 SSH 配置同步 · 或手动新建</small></div>
        <div class="books-tools">
          <button class="btn outline" onclick="SFPM.syncSsh()">⤓ 同步 SSH 配置</button>
          <button class="btn primary" onclick="SFPM.openBookForm()">+ 新建书签</button>
        </div>
      </div>`;
    let grid;
    if (state.bookmarks.length === 0) {
      grid = `<div class="empty-state">
        <div class="glyph">⛁</div>
        <div style="font-weight:600;font-size:calc(16px * var(--ui-scale))">还没有书签</div>
        <div style="font-size:calc(13px * var(--ui-scale))">点「新建书签」手动添加，或「同步 SSH 配置」从 ~/.ssh/config 导入</div>
      </div>`;
    } else {
      grid = `<div class="book-grid">` + state.bookmarks.map((b) => `
        <div class="book-card">
          <div class="book-top">
            <div class="book-avatar">${escapeHtml((b.name || b.host || "?").charAt(0).toUpperCase())}</div>
            <div style="min-width:0">
              <div class="book-name">${escapeHtml(b.name || b.host)}</div>
              <div class="book-meta">${escapeHtml(b.username)}@${escapeHtml(b.host)}:${b.port}</div>
            </div>
          </div>
          <div class="book-actions">
            <button class="btn primary sm" onclick="SFPM.connect('${b.id}')">连接</button>
            <button class="btn ghost sm" onclick="SFPM.openBookForm('${b.id}')">编辑</button>
            <button class="btn ghost sm" onclick="SFPM.testBook('${b.id}')">测试</button>
            <div class="spacer"></div>
            <button class="btn danger sm" onclick="SFPM.delBook('${b.id}')">删除</button>
          </div>
        </div>`).join("") + `</div>`;
    }
    v.innerHTML = header + grid;
  }

  // ---------- 书签表单 ----------
  function openBookForm(id) {
    const b = id ? state.bookmarks.find((x) => x.id === id) : null;
    openModal(`
      <div class="modal-title">${b ? "编辑书签" : "新建书签"}</div>
      <div class="modal-body">
        <div class="field"><label>名称</label><input id="f-name" value="${b ? escapeAttr(b.name) : ""}" placeholder="如 host-a 数据机"></div>
        <div class="field-row">
          <div class="field"><label>主机</label><input id="f-host" value="${b ? escapeAttr(b.host) : ""}" placeholder="192.168.1.10"></div>
          <div class="field"><label>端口</label><input id="f-port" type="number" value="${b ? b.port : 22}"></div>
        </div>
        <div class="field"><label>用户名</label><input id="f-user" value="${b ? escapeAttr(b.username) : ""}" placeholder="root"></div>
        <div class="field">
          <label>认证方式</label>
          <select id="f-auth">
            <option value="key" ${(b && b.auth_method === "key") || !b ? "selected" : ""}>SSH 密钥</option>
            <option value="password" ${b && b.auth_method === "password" ? "selected" : ""}>密码</option>
          </select>
        </div>
        <div class="field"><label>密钥路径</label><input id="f-key" value="${b && b.key_path ? escapeAttr(b.key_path) : "~/.ssh/id_ed25519"}" placeholder="~/.ssh/id_ed25519"></div>
        <div class="field">
          <label>密码 <span style="color:var(--muted)">(${b ? "留空则不变" : "可选，存 macOS 钥匙串"})</span></label>
          <input id="f-pass" type="password" placeholder="••••••••">
        </div>
        <div class="field"><label>默认目录</label><input id="f-dir" value="${b ? escapeAttr(b.default_path || "/") : "/"}" placeholder="/root"></div>
      </div>
      <div class="modal-foot">
        <button class="btn" onclick="SFPM.closeModal()">取消</button>
        <button class="btn primary" onclick="SFPM.saveBook('${id || ""}')">保存</button>
      </div>
    `);
    // 认证方式切换
    $("#f-auth").addEventListener("change", () => {
      const key = $("#f-key").closest(".field");
      key.style.display = $("#f-auth").value === "key" ? "" : "none";
    });
  }

  async function saveBook(id) {
    const data = {
      name: $("#f-name").value,
      host: $("#f-host").value,
      port: parseInt($("#f-port").value || "22", 10),
      username: $("#f-user").value,
      auth_method: $("#f-auth").value,
      key_path: $("#f-key").value || null,
      default_path: $("#f-dir").value || "/",
    };
    const pass = $("#f-pass").value;
    if (pass) data.password = pass;
    try {
      if (id) {
        await api("PUT", `/api/bookmarks/${id}`, data);
      } else {
        await api("POST", "/api/bookmarks", data);
      }
      closeModal();
      toast("书签已保存", "ok");
      loadBookmarks();
    } catch (e) {
      toast(e.message, "err");
    }
  }

  async function delBook(id) {
    if (!confirm("确定删除该书签？")) return;
    try {
      await api("DELETE", `/api/bookmarks/${id}`);
      toast("已删除", "ok");
      loadBookmarks();
    } catch (e) {
      toast(e.message, "err");
    }
  }

  async function testBook(id) {
    toast("测试中...");
    try {
      const r = await api("POST", `/api/bookmarks/${id}/test`);
      toast(r.message, r.ok ? "ok" : "err");
    } catch (e) {
      toast(e.message, "err");
    }
  }

  async function syncSsh() {
    toast("正在同步 ~/.ssh/config ...");
    try {
      const r = await api("POST", "/api/sync-ssh-config");
      toast(`同步完成：新建 ${r.created}，覆盖 ${r.updated}`, "ok");
      loadBookmarks();
    } catch (e) {
      toast(e.message, "err");
    }
  }

  // ---------- 文件浏览器 ----------
  async function connect(id) {
    toast("连接中...");
    try {
      const bm = state.bookmarks.find((x) => x.id === id);
      await api("POST", `/api/bookmarks/${id}/connect`);
      showBrowser(bm);
      toast("已连接", "ok");
    } catch (e) {
      toast(e.message, "err");
    }
  }

  async function loadDir() {
    if (state.loading) return;
    state.entries = []; state.total = 0;
    state.loading = true;
    const bm = state.current;
    const path = state.path;
    try {
      const r = await api("GET", `/api/bookmarks/${bm.id}/list?path=${encodeURIComponent(path)}`);
      state.entries = r.entries;
      state.total = r.total;
      renderBrowser();
    } catch (e) {
      $("#view-browser").innerHTML = `<div class="loading">${escapeHtml(e.message)}</div><div class="load-more"><button class="btn" onclick="SFPM.back()">返回书签</button></div>`;
      toast(e.message, "err");
    } finally {
      state.loading = false;
    }
  }

  function renderBrowser() {
    const bm = state.current;
    const crumbs = state.path.split("/").filter(Boolean);
    let crumbHtml = `<span class="crumb" onclick="SFPM.goto('/')">/</span>`;
    let acc = "";
    crumbs.forEach((c, i) => {
      acc += "/" + c;
      crumbHtml += `<span class="crumb sep">›</span><span class="crumb" onclick='SFPM.goto("${escapeAttrSingle(acc)}")'>${escapeHtml(c)}</span>`;
    });
    // 路径直接输入入口（点击弹输入框，回车跳转）
    crumbHtml += `<span class="crumb sep">›</span><span class="crumb path-input" title="输入路径跳转" onclick="SFPM.editPath()">✎ 输入路径</span>`;

    const visible = visibleEntries();

    const toolbar = `
      <div class="browser-toolbar">
        <button class="btn outline sm" onclick="SFPM.goto('${escapeAttrSingle(state.path)}')" title="刷新">⟳ 刷新</button>
        <button class="btn outline sm" onclick="SFPM.goUp()" title="上级">↑ 上级</button>
        <button class="btn outline sm" onclick="SFPM.newDir()">+ 新建目录</button>
        <button class="btn outline sm" onclick="SFPM.uploadFile()">↑ 上传</button>
        <button class="btn outline sm ${state.showHidden ? "active" : ""}" title="显示以 . 开头的隐藏文件/文件夹" onclick="SFPM.toggleHidden()">👁 显示隐藏项</button>
        <select class="sort-select sm" id="sort-key" title="排序字段" onchange="SFPM.setSort(this.value)">
          <option value="name" ${state.sortKey==="name"?"selected":""}>按名称</option>
          <option value="mtime" ${state.sortKey==="mtime"?"selected":""}>按时间</option>
          <option value="size" ${state.sortKey==="size"?"selected":""}>按大小</option>
        </select>
        <button class="btn outline sm" id="sort-dir" title="升序" onclick="SFPM.toggleSortDir()">${state.sortDir==="asc"?"↑":"↓"} ${state.sortDir==="asc"?"升":"降"}</button>
        <button class="btn outline sm" title="返回顶部" onclick="SFPM.scrollTopList()">⤒ 返回顶部</button>
        <span class="count" id="file-count">${visible.length} 项</span>
      </div>`;

    const selCount = state.selectedSet.size;
    const batchBar = `
      <div class="batch-bar" id="sel-bar" ${selCount ? "" : 'style="display:none"'}>
        <input type="checkbox" class="check-all" ${state.checkAll?"checked":""} onchange="SFPM.toggleCheckAll(this.checked)">
        <span class="count" id="sel-count">已选 ${selCount} 项</span>
        <div class="spacer" style="flex:1"></div>
        <button class="btn outline sm" onclick="SFPM.batchDownload()">批量下载</button>
        <button class="btn danger sm" onclick="SFPM.batchDelete()">批量删除</button>
      </div>`;

    let listHtml;
    // 不判 state.loading：loadDir 成功后就地渲染时它还是 true（finally 才复位），带上它这两句就永远出不来
    if (visible.length === 0) {
      listHtml = state.entries.length
        ? `<div class="loading">（这个目录只有隐藏项，用工具栏「显示隐藏项」可以看）</div>`
        : `<div class="loading">（空目录或还未加载）</div>`;
    } else {
      const rows = sortedEntries().map((e) => {
        const checked = state.selectedSet.has(e.path) ? "checked" : "";
        return `
        <div class="file-row ${checked?"selected":""}" data-path="${escapeAttr(e.path)}" data-dir="${e.is_dir}">
          <div class="file-check"><input type="checkbox" ${checked}></div>
          <div class="file-icon">${e.is_dir ? "📁" : fileIcon(e.name)}</div>
          <div class="file-name ${e.is_dir ? "dir" : ""}" ${e.is_dir ? `onclick="SFPM.openDir('${escapeAttrSingle(e.path)}')"` : `ondblclick="SFPM.previewFile('${escapeAttrSingle(e.path)}')" `}>${escapeHtml(e.name)}</div>
          <div class="file-size">${e.is_dir ? "-" : fmtSize(e.size)}</div>
          <div class="file-mtime">${fmtTime(e.mtime)}</div>
          <div class="file-ops">
            ${e.is_dir ? "" : `<button class="btn sm" title="预览" onclick="event.stopPropagation();SFPM.previewFile('${escapeAttrSingle(e.path)}')">👁</button>`}
            <button class="btn sm" title="下载" onclick="event.stopPropagation();SFPM.downloadOne('${escapeAttrSingle(e.path)}')">↓</button>
            <button class="btn sm" title="重命名" onclick="event.stopPropagation();SFPM.renameItem('${escapeAttrSingle(e.path)}')">✎</button>
            <button class="btn danger sm" title="删除" onclick="event.stopPropagation();SFPM.deleteItem('${escapeAttrSingle(e.path)}')">🗑</button>
          </div>
        </div>`;
      }).join("");
      listHtml = `<div class="file-list" id="file-list">${rows}</div>`;
    }

    $("#view-browser").innerHTML = `
      <div class="browser-head">
        <div class="crumbs">${crumbHtml}</div>
        ${toolbar}
        ${batchBar}
      </div>
      ${listHtml}
    `;
    indexRows();
  }

  // 排序后的展示顺序：目录永远优先；目录内部与文件内部各自按 sortKey/sortDir 排序
  // 先算出比较键再排：省掉每次比较里的 toLowerCase（两万个文件约 25 万次比较）
  function sortedEntries() {
    const key = state.sortKey;
    const mul = state.sortDir === "desc" ? -1 : 1;
    const src = visibleEntries();
    const deco = new Array(src.length);
    for (let i = 0; i < src.length; i++) {
      const e = src[i];
      let k;
      if (key === "mtime") k = e.mtime || 0;
      else if (key === "size") k = e.size || 0;
      else k = e.name.toLowerCase();
      deco[i] = { e, k, i, d: e.is_dir ? 0 : 1 };
    }
    deco.sort((a, b) => {
      if (a.d !== b.d) return a.d - b.d;      // 目录优先，跟升降序无关
      if (a.k < b.k) return -mul;
      if (a.k > b.k) return mul;
      return a.i - b.i;                       // 键相同就保持原顺序
    });
    return deco.map((x) => x.e);
  }

  // ---------- 大目录：勾选走增量更新（不再整表重建） ----------
  // 每次重建列表时刷新「路径 → 行元素」索引，勾选/全选只改必要的行
  let rowIndex = new Map();
  function indexRows() {
    rowIndex = new Map();
    const list = $("#file-list");
    if (!list) return;
    for (const row of list.children) rowIndex.set(row.dataset.path, row);
  }
  function paintRow(path, checked) {
    const row = rowIndex.get(path);
    if (!row) return;
    row.classList.toggle("selected", checked);
    const cb = row.querySelector('input[type="checkbox"]');
    if (cb && cb.checked !== checked) cb.checked = checked;
  }
  function updateSelBar() {
    const n = state.selectedSet.size;
    const bar = $("#sel-bar");
    if (bar) bar.style.display = n > 0 ? "" : "none";
    const cnt = $("#sel-count");
    if (cnt) cnt.textContent = `已选 ${n} 项`;
    const all = $(".check-all");
    if (all) all.checked = state.checkAll;
  }

  function goto(path) {
    state.path = path; state.selected = null;
    loadDir();
    syncUrl();
  }

  function goUp() {
    const parts = state.path.replace(/\/+$/, "").split("/");
    parts.pop();
    goto(parts.join("/") || "/");
  }


  function openDir(path) { goto(path); }

  function editPath() {
    const p = prompt("输入要跳转的路径:", state.path || "/");
    if (p === null) return;
    const norm = p.startsWith("/") ? p : "/" + p;
    if (norm === state.path) return;
    state.path = norm;
    loadDir();
    // 同步 URL
    syncUrl();
  }

  // ---------- URL 路由：http://127.0.0.1:9000/{hostkey}/{path} ----------
  function hostKeyOf(bm) {
    // 用书签名作为 URL 段的 key（优先 name，其次 host）
    return encodeURIComponent(bm.name || bm.host);
  }

  function syncUrl() {
    // 更新地址栏为 /{host}/{path}，支持直接访问/分享深链接
    if (!state.current) return;
    const hostKey = hostKeyOf(state.current);
    const path = (state.path || "/").replace(/^\/+/, "").replace(/\/+$/, "");
    const newUrl = `/${hostKey}` + (path ? "/" + path : "");
    history.replaceState(null, "", newUrl);
  }

  function findBookmarkByKey(key) {
    const k = decodeURIComponent(key).toLowerCase();
    return state.bookmarks.find(b => (b.name || "").toLowerCase() === k || (b.host || "").toLowerCase() === k) || null;
  }

  async function parseUrlOnLoad() {
    // 兼容两种 URL：pathname 路由 /{host}/{path} 与 hash 路由 #/host/path
    let route = null;
    if (location.hash.startsWith("#/")) {
      route = location.hash.slice(1);          // "/host/path"
    } else if (location.pathname && location.pathname !== "/") {
      route = location.pathname;
    }
    if (!route || route === "/") return;
    const parts = route.split("/").filter(Boolean);
    if (parts.length < 1) return;
    if (!state.bookmarks.length) await loadBookmarks();
    const hostKey = parts[0];
    const sub = parts.slice(1).join("/");
    const bm = findBookmarkByKey(hostKey);
    if (!bm) return; // 找不到主机，留在书签页
    // 先连接（复用会话），再进入浏览器并 goto 到对应路径
    try {
      await api("POST", `/api/bookmarks/${bm.id}/connect`);
      showBrowser(bm, "/" + sub);
      syncUrl();
    } catch (e) {
      toast(e.message, "err");
    }
  }


  async function newDir() {
    const p = prompt("目录名（相对当前目录）:", "新建目录");
    if (!p) return;
    const path = joinPath(state.path, p);
    try {
      await api("POST", `/api/bookmarks/${state.current.id}/mkdir?path=${encodeURIComponent(path)}`);
      toast("已新建", "ok");
      loadDir();
    } catch (e) { toast(e.message, "err"); }
  }

  async function uploadFile() {
    const input = $("#file-input");
    input.onchange = async () => {
      for (const f of input.files) {
        const fd = new FormData();
        fd.append("file", f);
        try {
          await api("POST", `/api/bookmarks/${state.current.id}/upload?path=${encodeURIComponent(state.path)}`, fd, true);
          toast(`已上传 ${f.name}`, "ok");
        } catch (e) { toast(`上传 ${f.name} 失败: ${e.message}`, "err"); }
      }
      input.value = "";
      loadDir();
    };
    input.click();
  }

  async function downloadOne(path) {
    const url = `/api/bookmarks/${state.current.id}/download?path=${encodeURIComponent(path)}`;
    const a = document.createElement("a");
    a.href = url;
    a.download = path.split("/").pop();
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  async function renameItem(path) {
    const old = path.split("/").pop();
    const newName = prompt("新名称:", old);
    if (!newName || newName === old) return;
    const newPath = joinPath(path.slice(0, path.lastIndexOf("/")), newName);
    try {
      await api("POST", `/api/bookmarks/${state.current.id}/rename?old=${encodeURIComponent(path)}&new=${encodeURIComponent(newPath)}`);
      toast("已重命名", "ok");
      loadDir();
    } catch (e) { toast(e.message, "err"); }
  }

  function back() { showBooks(); }

  // ---------- 删除（流式进度） ----------
  // 大文件夹/多选删除走一个 NDJSON 流：服务端边删边报数，前端可取消。
  // 单项失败不影响其余项；取消/关页面时请求断开，服务端最多再删完当前一条。
  function baseName(p) {
    const parts = String(p || "").split("/").filter(Boolean);
    return parts.length ? parts[parts.length - 1] : "";
  }

  function selectedEntries() {
    return visibleEntries().filter((e) => state.selectedSet.has(e.path));
  }

  // 保持与当前排序一致：取 entries 中在 selectedSet 里的，目录优先
  function selectedPaths() {
    return selectedEntries().map((e) => e.path);
  }

  async function deleteItem(path) {
    if (state.deleteBusy) return;
    const e = state.entries.find((x) => x.path === path);
    const name = baseName(path);
    const msg = e && e.is_dir
      ? `删除文件夹「${name}」？\n里面的所有内容会一起删除，且无法恢复。`
      : `删除「${name}」？\n删除后无法恢复。`;
    if (!confirm(msg)) return;
    await runDelete([path]);
  }

  async function batchDelete() {
    if (state.deleteBusy) return;
    const entries = selectedEntries();
    if (entries.length === 0) return;
    const dirs = entries.filter((e) => e.is_dir).length;
    const msg = `删除选中的 ${entries.length} 项${dirs ? `（含 ${dirs} 个文件夹，内部文件会一起删除）` : ""}？\n删除后无法恢复。`;
    if (!confirm(msg)) return;
    await runDelete(entries.map((e) => e.path));
  }

  // 总数未知（大文件夹边删边数），进度条用流动条纹表示仍在进行
  function openDeleteProgress(total) {
    openModal(`
      <div class="modal-title">正在删除</div>
      <div class="modal-body">
        <div class="del-current" id="del-current">准备中…</div>
        <div class="del-meter"><span></span></div>
        <div class="del-count" id="del-count">已删除 0 个文件</div>
        <div class="del-sub" id="del-sub">共 ${total} 项 · 大文件夹需要一些时间</div>
      </div>
      <div class="modal-foot">
        <button class="btn danger" id="del-cancel" onclick="SFPM.cancelDelete()">取消</button>
      </div>`);
  }

  function paintDeleteProgress(stats) {
    const c = $("#del-count");
    if (c) c.textContent = `已删除 ${stats.files} 个文件${stats.dirs ? `、${stats.dirs} 个文件夹` : ""} · ${fmtSize(stats.bytes)}`;
  }

  function cancelDelete() {
    if (!state.deleteBusy) return;
    const btn = $("#del-cancel");
    if (btn) { btn.disabled = true; btn.textContent = "正在停止…"; }
    if (state.deleteAbort) state.deleteAbort.abort();
  }

  // 一项删完：行/选中集/计数即时更新，不等整批结束再刷列表
  function forgetEntry(path) {
    state.selectedSet.delete(path);
    const before = state.entries.length;
    state.entries = state.entries.filter((e) => e.path !== path);
    const row = rowIndex.get(path);
    if (row) { row.remove(); rowIndex.delete(path); }
    if (state.entries.length === before) return;
    const cnt = $("#file-count");
    if (cnt) cnt.textContent = `${visibleEntries().length} 项`;
    const visible = visibleEntries();
    state.checkAll = visible.length > 0 && state.selectedSet.size === visible.length;
    updateSelBar();
  }

  async function runDelete(paths) {
    if (state.deleteBusy || paths.length === 0) return;
    state.deleteBusy = true;
    const ctrl = new AbortController();
    state.deleteAbort = ctrl;
    const total = paths.length;
    const stats = { files: 0, dirs: 0, bytes: 0 };
    const failed = [];
    let cancelled = false;
    openDeleteProgress(total);

    try {
      const res = await fetch(`/api/bookmarks/${state.current.id}/delete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ paths }),
        signal: ctrl.signal,
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `请求失败 (${res.status})`);
      }
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let nl;
        while ((nl = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, nl).trim();
          buf = buf.slice(nl + 1);
          if (!line) continue;
          let ev;
          try { ev = JSON.parse(line); } catch (_) { continue; }
          if (ev.type === "progress" || ev.type === "done") {
            stats.files = ev.files; stats.dirs = ev.dirs; stats.bytes = ev.bytes;
            paintDeleteProgress(stats);
          } else if (ev.type === "item_start") {
            const cur = $("#del-current");
            if (cur) cur.textContent = `正在删除：${baseName(ev.path)}`;
            const sub = $("#del-sub");
            if (sub) sub.textContent = total > 1 ? `第 ${ev.index}/${ev.total} 项` : "大文件夹需要一些时间";
          } else if (ev.type === "item_done") {
            if (ev.ok) forgetEntry(ev.path);
            else if (ev.cancelled) cancelled = true;
            else failed.push({ path: ev.path, error: ev.error || "未知错误" });
          } else if (ev.type === "aborted") {
            failed.push({ path: "", error: ev.error || "连接中断" });
          } else if (ev.type === "done") {
            cancelled = cancelled || !!ev.cancelled;
          }
        }
      }
    } catch (e) {
      if (e.name === "AbortError") cancelled = true;
      else failed.push({ path: "", error: e.message || String(e) });
    } finally {
      state.deleteBusy = false;
      state.deleteAbort = null;
      finishDelete(stats, failed, cancelled);
    }
  }

  function finishDelete(stats, failed, cancelled) {
    const summary = `已删除 ${stats.files} 个文件${stats.dirs ? `、${stats.dirs} 个文件夹` : ""} · ${fmtSize(stats.bytes)}`;
    if (!failed.length && !cancelled) {
      closeModal();
      toast(summary, "ok");
      if (rowIndex.size === 0) renderBrowser();   // 整页删空：显示空目录提示
      return;
    }
    const shown = failed.slice(0, 20);
    const rest = failed.length - shown.length;
    const listHtml = shown.map((f) => {
      const name = baseName(f.path);
      return `<div>${name ? escapeHtml(name) + "：" : ""}${escapeHtml(f.error || "")}</div>`;
    }).join("");
    openModal(`
      <div class="modal-title">${cancelled ? "已取消删除" : "删除未全部完成"}</div>
      <div class="modal-body">
        <div>${escapeHtml(summary)}</div>
        ${failed.length ? `<div class="del-sub">${failed.length} 项未删除：</div>
          <div class="del-fails">${listHtml}${rest > 0 ? `<div>…还有 ${rest} 项</div>` : ""}</div>` : ""}
        ${cancelled ? `<div class="del-sub">取消后剩余内容保留，已删掉的不会恢复。</div>` : ""}
      </div>
      <div class="modal-foot"><button class="btn primary" onclick="SFPM.closeModal()">知道了</button></div>`);
    // 失败/取消的目录可能只删了一半：重新列一次，让界面和服务器一致
    loadDir();
  }

  // ---------- 隐藏项 ----------
  // 判据：名字以「.」开头。后端照旧返回全目录，这里只决定显示哪些，切换零网络往返。
  function isHiddenName(name) {
    return String(name || "").startsWith(".");
  }

  function visibleEntries() {
    return state.showHidden ? state.entries : state.entries.filter((e) => !isHiddenName(e.name));
  }

  function initHidden() {
    state.showHidden = localStorage.getItem("cybercat_showhidden") !== "0";
  }

  function toggleHidden() {
    state.showHidden = !state.showHidden;
    localStorage.setItem("cybercat_showhidden", state.showHidden ? "1" : "0");
    pruneSelection();
    renderBrowser();
  }

  // 选中的路径收敛回可见项，并修正全选状态
  function pruneSelection() {
    const visible = visibleEntries();
    const keep = new Set(visible.map((e) => e.path));
    for (const p of [...state.selectedSet]) if (!keep.has(p)) state.selectedSet.delete(p);
    state.checkAll = visible.length > 0 && state.selectedSet.size === visible.length;
  }

  // ---------- 排序 ----------
  // 实测：2 万行时「重建整表」比「搬动已有节点」快得多（1.1s vs 22–28s），故排序仍走重建
  // 换排序键/换升降序：不重建 DOM，只把现成的行按新顺序摆一遍。
  // 重建整表要重新解析 24 万个节点（两万行），原地重排只动已有的行。
  function applySort() {
    const list = $("#file-list");
    const sel = $("#sort-key");
    if (sel) sel.value = state.sortKey;
    const dir = $("#sort-dir");
    if (dir) dir.textContent = state.sortDir === "asc" ? "↑ 升" : "↓ 降";
    if (!list || rowIndex.size === 0) return renderBrowser();   // 还没有列表：老实整表渲染
    const frag = document.createDocumentFragment();
    for (const e of sortedEntries()) {
      const row = rowIndex.get(e.path);
      if (row) frag.appendChild(row);
    }
    if (frag.childElementCount !== rowIndex.size) return renderBrowser();  // 对不上（数据与 DOM 不一致）就重建
    list.replaceChildren(frag);                                 // 一次插入，只排一次版
  }
  function setSort(key) { state.sortKey = key; applySort(); }
  function toggleSortDir() { state.sortDir = state.sortDir === "asc" ? "desc" : "asc"; applySort(); }

  // 返回顶部：现在整页在滚（列表跟着页面长），列表要是自己滚也一起管上。
  // 不做平滑动画：屏外的行本来就不参与排版，跳一下就到位；而且动画在后台标签页里不会跑。
  function scrollTopList() {
    const list = $("#file-list");
    if (list && list.scrollHeight > list.clientHeight + 1) list.scrollTop = 0;
    const sc = document.scrollingElement || document.documentElement;
    if (sc) sc.scrollTop = 0;
  }

  // ---------- 多选 ----------
  function toggleCheck(path, checked) {
    if (checked) state.selectedSet.add(path);
    else state.selectedSet.delete(path);
    const visible = visibleEntries();
    state.checkAll = visible.length > 0 && state.selectedSet.size === visible.length;
    paintRow(path, checked);
    updateSelBar();
  }
  function toggleCheckAll(checked) {
    state.selectedSet.clear();
    if (checked) visibleEntries().forEach(e => state.selectedSet.add(e.path));
    state.checkAll = checked && visibleEntries().length > 0;
    // 逐行改状态，不重建列表：大目录下全选从「几秒」降到「几十毫秒」，且滚动位置不丢
    const list = $("#file-list");
    if (list) {
      for (const row of list.children) row.classList.toggle("selected", checked);
      for (const cb of list.querySelectorAll(".file-check input")) {
        if (cb.checked !== checked) cb.checked = checked;
      }
    }
    updateSelBar();
  }
  async function batchDownload() {
    const paths = selectedPaths().filter(p => !state.entries.find(e => e.path === p)?.is_dir);
    if (paths.length === 0) { toast("请选择要下载的文件", "err"); return; }
    for (const p of paths) {
      const url = `/api/bookmarks/${state.current.id}/download?path=${encodeURIComponent(p)}`;
      const a = document.createElement("a");
      a.href = url; a.download = p.split("/").pop();
      document.body.appendChild(a); a.click(); a.remove();
    }
  }

  // ---------- 预览 ----------
  // 打开即出全屏白膜 + 进度条，随时可取消。
  // 卡片先建好、白膜盖在它上面：媒体元素必须在文档里才会开始加载，
  // 所以不能先挂在一个游离节点上等 ready 再插进来。
  const PREVIEW_TEXT_LIMIT = 256 * 1024;        // 文本类只取前 256KB
  const PREVIEW_BLOB_MAX = 64 * 1024 * 1024;    // 图片/音频超过这个大小就直接挂 URL，不整个进内存
  const PREVIEW_HL_MAX = 200 * 1024;            // 再长就纯文本显示，别让着色卡住主线程
  const PREVIEW_SLOW_MS = 10000;                // 超过这个时间追加「网络较慢」提示
  const PREVIEW_MAX_MB = 512;                   // 超过就不预览；后端同一个上限，这边只是早一步拦住

  const IMAGE_EXT = new Set(["png", "jpg", "jpeg", "gif", "webp", "avif", "bmp", "ico", "svg"]);
  const VIDEO_EXT = new Set(["mp4", "m4v", "webm", "ogv", "mov", "mkv", "avi"]);
  const AUDIO_EXT = new Set(["mp3", "wav", "ogg", "oga", "opus", "flac", "m4a", "aac"]);
  const PDF_EXT = new Set(["pdf"]);
  const TEXT_EXT = new Set(["txt", "text", "log", "md", "markdown", "rst", "csv", "tsv", "ini", "conf", "cfg", "env",
    "yaml", "yml", "toml", "xml", "html", "htm", "css", "scss", "less", "js", "mjs", "cjs", "jsx", "ts", "tsx",
    "vue", "svelte", "py", "rb", "php", "go", "rs", "java", "kt", "swift", "c", "h", "cc", "cpp", "hpp", "cs",
    "m", "mm", "scala", "pl", "lua", "r", "sql", "sh", "bash", "zsh", "fish", "ps1", "bat", "dockerfile",
    "makefile", "gitignore", "editorconfig", "properties", "json"]);
  const MIME_BY_EXT = {
    png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", webp: "image/webp",
    avif: "image/avif", bmp: "image/bmp", ico: "image/x-icon", svg: "image/svg+xml",
    mp3: "audio/mpeg", wav: "audio/wav", ogg: "audio/ogg", oga: "audio/ogg", opus: "audio/ogg",
    flac: "audio/flac", m4a: "audio/mp4", aac: "audio/aac",
  };

  let previewAbort = null;      // 字节流的取消句柄
  let previewUrl = null;        // Blob URL，关窗时吊销
  let previewMedia = null;      // <video>/<audio>，关窗时停播并断流
  let previewSlowTimer = null;  // 慢速提示计时器
  let previewPath = null;       // 正在预览的远端路径（卡片里的「下载」要用它）
  let previewSeq = 0;           // 预览代次：关窗/重开时自增，作废在途回调

  function extOf(name) {
    const i = String(name || "").lastIndexOf(".");
    return i > 0 ? name.slice(i + 1).toLowerCase() : String(name || "").toLowerCase();
  }
  function previewKind(name) {
    const ext = extOf(name);
    if (IMAGE_EXT.has(ext)) return "image";
    if (VIDEO_EXT.has(ext)) return "video";
    if (AUDIO_EXT.has(ext)) return "audio";
    if (PDF_EXT.has(ext)) return "pdf";
    if (ext === "json") return "json";
    if (TEXT_EXT.has(ext)) return "code";
    return "trytext";   // 不认识的后缀：先当文本试试，读到 0 字节就判定二进制
  }
  const KIND_LABEL = { image: "图片", video: "视频", audio: "音频", pdf: "PDF", json: "JSON", code: "文本", trytext: "文件", binary: "二进制" };

  function previewUrlFor(path) {
    return `/api/bookmarks/${state.current.id}/preview?path=${encodeURIComponent(path)}`;
  }

  // 卡片 + 白膜：白膜盖在卡片上，内容就绪后再揭掉
  function openPreviewSheet(name, size, kind, determinate) {
    if (previewSlowTimer) { clearTimeout(previewSlowTimer); previewSlowTimer = null; }
    const m = $("#modal");
    m.classList.add("preview");
    m.innerHTML = `
      <div class="modal-card preview-card">
        <div class="preview-head">
          <span class="preview-name" title="${escapeAttr(name)}">${escapeHtml(name)}</span>
          <span class="preview-meta">${fmtSize(size)} · ${KIND_LABEL[kind] || "文件"}</span>
          <div class="spacer" style="flex:1"></div>
          ${kind === "video" ? `<button class="btn outline sm" id="preview-transcode" onclick="SFPM.toggleVideoSource()">转码播放</button>` : ""}
          <button class="btn outline sm" onclick="SFPM.downloadOne('${escapeAttrSingle(previewPath || "")}')">下载</button>
          <button class="btn sm" onclick="SFPM.closePreview()">关闭</button>
        </div>
        <div class="preview-body" id="preview-body"></div>
      </div>
      <div class="preview-veil" id="preview-veil">
        <div class="veil-card">
          <div class="veil-name" title="${escapeAttr(name)}">${escapeHtml(name)}</div>
          <div class="veil-meta">${fmtSize(size)} · ${KIND_LABEL[kind] || "文件"}</div>
          ${determinate
            ? `<div class="veil-bar"><i id="veil-fill"></i></div>`
            : `<div class="veil-spinner" role="status" aria-label="正在准备">
                 <svg viewBox="0 0 32 32" aria-hidden="true">
                   <circle class="track" cx="16" cy="16" r="14"/>
                   <circle class="arc" cx="16" cy="16" r="14"/>
                 </svg>
               </div>`}
          <div class="veil-hint" id="veil-hint">${determinate ? "正在读取…" : "正在读取媒体信息…"}</div>
          <button class="btn outline" id="veil-cancel" onclick="SFPM.cancelPreview()">取消预览</button>
        </div>
      </div>`;
    m.classList.remove("hidden");
    previewSlowTimer = setTimeout(() => {
      const h = $("#veil-hint");
      if (h) {
        h.textContent = determinate
          ? "还在加载…网络较慢，可取消后改用下载"
          : "还没拿到媒体信息，可能编码不支持；取消后可点右上角「下载」用本地播放器打开";
      }
    }, PREVIEW_SLOW_MS);
  }

  function liftVeil() {
    if (previewSlowTimer) { clearTimeout(previewSlowTimer); previewSlowTimer = null; }
    const v = $("#preview-veil");
    if (v) v.remove();
  }
  function setPreviewBody(html) {
    const b = $("#preview-body");
    if (b) b.innerHTML = html;
  }
  function appendPreviewBody(node, centered) {
    const b = $("#preview-body");
    if (!b) return;
    if (centered) {
      const wrap = document.createElement("div");
      wrap.className = "preview-center";
      wrap.appendChild(node);
      b.appendChild(wrap);
    } else {
      b.appendChild(node);
    }
  }
  function setVeilProgress(loaded, total) {
    const fill = $("#veil-fill");
    if (!fill || !total) return;
    fill.style.width = Math.min(100, Math.round((loaded / total) * 100)) + "%";
  }
  // 出错就地把白膜变成错误卡（保留关闭）
  function previewFailed(msg) {
    if (previewSlowTimer) { clearTimeout(previewSlowTimer); previewSlowTimer = null; }
    const hint = $("#veil-hint");
    if (hint) { hint.className = "veil-hint err"; hint.textContent = msg; }
    const bar = $("#preview-veil .veil-bar, #preview-veil .veil-spinner");
    if (bar) bar.remove();
    const btn = $("#veil-cancel");
    if (btn) { btn.textContent = "关闭"; btn.onclick = () => SFPM.closePreview(); }
  }

  // 白膜已经揭开后的提示：贴在视频上方，不打断播放
  function clearPreviewNotice() {
    const d = $("#preview-hint");
    if (d) d.remove();
  }

  function previewNotice(msg) {
    const body = $("#preview-body");
    if (!body || $("#preview-hint")) return;
    const d = document.createElement("div");
    d.id = "preview-hint";
    d.className = "preview-notice";
    d.textContent = msg;
    body.prepend(d);
  }

  // 换视频源：direct 直连原文件，transcode 实时转码流
  function transcodeUrl() {
    return `/api/bookmarks/${state.current.id}/transcode?path=${encodeURIComponent(previewPath)}`;
  }

  // 准备远端 ffmpeg，失败时返回原因
  async function probeTranscode() {
    try {
      const res = await fetch(`${transcodeUrl()}&probe=1`);
      if (res.ok) return { ok: true };
      const j = await res.json().catch(() => ({}));
      return { ok: false, msg: j.detail || `准备转码失败（HTTP ${res.status}）` };
    } catch (e) {
      return { ok: false, msg: `准备转码失败：${e.message}` };
    }
  }

  function attachVideoListeners(v, token, tr, btn) {
    if (typeof v._onCanplay === "function") v.removeEventListener("canplay", v._onCanplay);
    if (typeof v._onError === "function") v.removeEventListener("error", v._onError);
    v._onCanplay = () => {
      if (token !== previewSeq) return;
      if (tr) clearPreviewNotice();   // 开始播放后收起提示
      liftVeil();
      v.play().catch(() => { /* 自动播放被拦：用户按播放键即可 */ });
    };
    v._onError = () => {
      if (token !== previewSeq) return;
      if (!tr) {
        previewFailed("这个视频的编码浏览器播不了：点右上角「转码播放」或「下载」");
        return;
      }
      if (btn) btn.textContent = "重试转码";
      previewNotice("转码流没起来，正在查原因…");
      // 单独探一次，把服务器给的原因显示出来
      probeTranscode().then((r) => {
        if (token !== previewSeq || v._source !== "transcode") return;
        previewNotice(r.ok ? "转码流断了，点「重试转码」再来一次，或「直连播放」看原文件。" : r.msg);
      });
    };
    v.addEventListener("canplay", v._onCanplay, { once: true });
    v.addEventListener("error", v._onError, { once: true });
  }

  function useVideoSource(v, source, token) {
    const btn = $("#preview-transcode");
    const tr = source === "transcode";
    v._source = source;            // 当前源（direct / transcode）
    if (!tr) {
      clearPreviewNotice();
      if (btn) { btn.textContent = "转码播放"; btn.disabled = false; }
      attachVideoListeners(v, token, false, btn);
      v.src = state.videoUrl;
      return;
    }
    if (btn) { btn.textContent = "直连播放"; btn.disabled = false; }
    const hint = $("#veil-hint");
    if (hint && !hint.classList.contains("err")) hint.textContent = "正在准备转码流…";
    attachVideoListeners(v, token, true, btn);
    v.src = transcodeUrl();
  }

  // 顶栏按钮：在「转码播放」和「直连播放」之间切换
  function toggleVideoSource() {
    if (!previewMedia) return;
    const btn = $("#preview-transcode");
    const toTranscode = !(btn && (btn.textContent === "直连播放" || btn.textContent === "重试转码"));
    useVideoSource(previewMedia, toTranscode ? "transcode" : "direct", previewSeq);
  }

  function loadVideo(url, name, size, token) {
    const v = document.createElement("video");
    v.className = "preview-media"; v.controls = true; v.playsInline = true;
    v.preload = "auto";        // 提前缓冲整段
    previewMedia = v;
    state.videoUrl = url;
    appendPreviewBody(v, true);   // 必须在设 src 前进文档，游离的媒体元素不会开始加载
    useVideoSource(v, state.transcodeDefault ? "transcode" : "direct", token);
  }

  function loadAudio(src, name, size, token) {
    const a = document.createElement("audio");
    a.className = "preview-media audio"; a.controls = true;
    a.preload = "metadata";
    previewMedia = a;
    appendPreviewBody(a, true);
    a.addEventListener("loadedmetadata", () => {
      if (token !== previewSeq) return;
      liftVeil();
      a.play().catch(() => { /* 同上 */ });
    }, { once: true });
    a.addEventListener("error", () => { if (token === previewSeq) previewFailed("这个音频浏览器播不了，可以点右上角「下载」用本地播放器打开"); }, { once: true });
    a.src = src;
  }

  function loadImage(src, name, size, token) {
    const img = document.createElement("img");
    img.className = "preview-img"; img.alt = name;
    appendPreviewBody(img, true);
    const ok = () => { if (token === previewSeq) liftVeil(); };
    const bad = () => { if (token === previewSeq) previewFailed("图片加载失败，可能不是有效图片"); };
    img.addEventListener("load", ok, { once: true });
    img.addEventListener("error", bad, { once: true });
    img.src = src;
    // 不用 img.decode()：后台标签页里它的 promise 会一直挂着不 settle（实测 naturalWidth 已就绪仍不返回）
    if (img.complete && img.naturalWidth) ok();
  }

  function loadPdf(url, name, size, token) {
    const f = document.createElement("iframe");
    f.className = "preview-frame"; f.title = "PDF 预览"; f.src = url;
    appendPreviewBody(f, false);
    f.addEventListener("load", () => { if (token === previewSeq) liftVeil(); }, { once: true });
    // iframe 没有可靠的 error 事件：一直读不出来就让用户按「取消预览」
  }

  function stopMedia() {
    if (!previewMedia) return;
    try { previewMedia.pause(); } catch (e) { /* 忽略 */ }
    try { previewMedia.removeAttribute("src"); previewMedia.load(); } catch (e) { /* 忽略 */ }
    previewMedia = null;
  }
  function dropObjectUrl() {
    if (previewUrl) { URL.revokeObjectURL(previewUrl); previewUrl = null; }
  }
  function blobUrl(bytes, name) {
    dropObjectUrl();
    const type = MIME_BY_EXT[extOf(name)] || "application/octet-stream";
    previewUrl = URL.createObjectURL(new Blob([bytes], { type }));
    return previewUrl;
  }

  // ---- 字节流：读 + 进度 + 可取消 ----
  async function readBytes(res, expect, onProgress) {
    const reader = res.body.getReader();
    const chunks = [];
    let loaded = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      loaded += value.length;
      if (onProgress) onProgress(loaded, expect);
    }
    const out = new Uint8Array(loaded);
    let off = 0;
    for (const c of chunks) { out.set(c, off); off += c.length; }
    return out;
  }

  async function previewFile(path, name, size) {
    const entry = state.entries.find((e) => e.path === path);
    name = name || (entry ? entry.name : path.split("/").pop());
    size = size == null ? (entry ? entry.size : 0) : size;
    const kind = previewKind(name);
    closePreview();                       // 清掉上一个（含未完成的流）
    previewPath = path;
    const token = ++previewSeq;

    const url = previewUrlFor(path);
    // 超过大小上限：一个字节都不读，直接在卡片里说清楚（后端也会拒，这里只是早一步）
    if (size > PREVIEW_MAX_MB * 1024 * 1024) {
      openPreviewSheet(name, size, kind, true);
      setPreviewBody(`<div class="preview-center"><div class="preview-notice">${fmtSize(size)} 超过 ${PREVIEW_MAX_MB} MB 的预览上限。<br>点右上角「下载」拿下去看。</div></div>`);
      liftVeil();
      return;
    }
    // 视频 / PDF / 超大图片音频：不整段进内存，直接把预览地址挂给元素
    if (kind === "video" || kind === "pdf" || ((kind === "image" || kind === "audio") && size > PREVIEW_BLOB_MAX)) {
      openPreviewSheet(name, size, kind, false);
      if (kind === "video") loadVideo(url, name, size, token);
      else if (kind === "pdf") loadPdf(url, name, size, token);
      else if (kind === "image") loadImage(url, name, size, token);
      else loadAudio(url, name, size, token);
      return;
    }

    // 其余一律走字节流：能算真实进度，也能真的取消
    const textual = kind !== "image" && kind !== "audio";
    const limit = textual ? PREVIEW_TEXT_LIMIT : null;
    const truncated = !!(limit && size > limit);
    const expect = truncated ? limit : (size || 0);
    openPreviewSheet(name, size, kind, expect > 0);
    try {
      previewAbort = new AbortController();
      const headers = truncated ? { Range: `bytes=0-${limit - 1}` } : {};
      const res = await fetch(url, { headers, signal: previewAbort.signal });
      if (!res.ok && res.status !== 206) throw new Error(`读取失败：HTTP ${res.status}`);
      const bytes = await readBytes(res, expect, setVeilProgress);
      if (token !== previewSeq) return;    // 期间被取消/换了目标
      if (kind === "image") return loadImage(blobUrl(bytes, name), name, size, token);
      if (kind === "audio") return loadAudio(blobUrl(bytes, name), name, size, token);
      renderTextPreview(bytes, name, size, kind, truncated, token);
    } catch (e) {
      if (e && e.name === "AbortError") return;   // 用户取消：收尾交给 closePreview
      previewFailed(e && e.message ? e.message : String(e));
    } finally {
      previewAbort = null;
    }
  }

  // ---- 文本/代码/JSON ----
  function looksBinary(bytes) {
    const n = Math.min(bytes.length, 8192);
    for (let i = 0; i < n; i++) if (bytes[i] === 0) return true;
    return false;
  }

  function renderTextPreview(bytes, name, size, kind, truncated, token) {
    if (looksBinary(bytes)) {
      setPreviewBody('<div class="preview-center"><div class="preview-notice">这个文件是二进制，没法当文本预览。<br>点右上角「下载」拿下去看。</div></div>');
      liftVeil();
      return;
    }
    let code = new TextDecoder("utf-8", { fatal: false }).decode(bytes);
    const ext = extOf(name);
    let jsonError = null;
    if (ext === "json") {
      try { code = JSON.stringify(JSON.parse(code), null, 2); }
      catch (e) { jsonError = e && e.message ? e.message : String(e); }
    }
    const meta = [];
    if (truncated) meta.push(`仅显示前 ${fmtSize(PREVIEW_TEXT_LIMIT)}（共 ${fmtSize(size)}）`);
    if (jsonError) meta.push(`JSON 解析失败：${jsonError}`);
    setPreviewBody(`${meta.length ? `<div class="preview-note">${escapeHtml(meta.join(" · "))}</div>` : ""}${codeBlockHtml(code, ext)}`);
    if (token === previewSeq) liftVeil();
  }

  function codeBlockHtml(code, ext) {
    const lines = code.split("\n");
    const gutter = lines.map((_, i) => i + 1).join("\n");
    const body = code.length <= PREVIEW_HL_MAX ? highlight(code, ext) : escapeHtml(code);
    return `<div class="code-wrap"><pre class="code-gutter" aria-hidden="true">${gutter}</pre><pre class="code-body">${body}</pre></div>`;
  }

  // ---- 轻量语法着色：一次扫描；先切分再逐段转义（先转义会把引号变成 &quot;，匹配就废了）----
  const HL_KEYWORDS = new Set(("def class return if else elif for while import from as try except finally with lambda " +
    "yield await async const let var function new this typeof instanceof null undefined true false None True False " +
    "and or not in is pass raise global nonlocal assert del break continue public private protected static final void " +
    "int float str bool list dict set tuple struct impl trait enum fn mut pub use mod match case switch default go " +
    "func type interface package defer chan select range map make union extern sizeof typedef namespace using " +
    "throw catch throws extends implements super self cls echo print printf len end then do done fi esac local export " +
    "readonly declare until when").split(" "));
  const HL_COMMENT = {
    hash: "#[^\\n]*",
    slash: "\\/\\/[^\\n]*|\\/\\*[\\s\\S]*?\\*\\/",
    dash: "--[^\\n]*",
    markup: "<!--[\\s\\S]*?-->",
  };
  const HL_HASH_EXT = new Set(["py", "rb", "sh", "bash", "zsh", "fish", "yaml", "yml", "toml", "ini", "conf", "cfg",
    "env", "properties", "dockerfile", "makefile", "gitignore", "editorconfig", "pl", "r", "ps1"]);
  const HL_DASH_EXT = new Set(["sql", "lua"]);
  const HL_MARKUP_EXT = new Set(["html", "htm", "xml", "svg", "vue", "svelte"]);

  function highlight(code, ext) {
    const comment = HL_MARKUP_EXT.has(ext) ? HL_COMMENT.markup
      : HL_DASH_EXT.has(ext) ? HL_COMMENT.dash
      : HL_HASH_EXT.has(ext) ? HL_COMMENT.hash
      : HL_COMMENT.slash;
    const re = new RegExp(
      '("""[\\s\\S]*?"""|\'\'\'[\\s\\S]*?\'\'\'|"(?:\\\\.|[^"\\\\\\n])*"|\'(?:\\\\.|[^\'\\\\\\n])*\'|`(?:\\\\.|[^`\\\\])*`)' +
      "|(" + comment + ")" +
      "|(\\b\\d[\\d_.eExXa-fA-F+-]*\\b)" +
      "|([A-Za-z_$][A-Za-z0-9_$]*)", "g");
    let out = "";
    let last = 0;
    let m;
    while ((m = re.exec(code))) {
      out += escapeHtml(code.slice(last, m.index));
      const tok = m[0];
      if (m[1]) out += `<span class="tok-s">${escapeHtml(tok)}</span>`;
      else if (m[2]) out += `<span class="tok-c">${escapeHtml(tok)}</span>`;
      else if (m[3]) out += `<span class="tok-n">${escapeHtml(tok)}</span>`;
      else if (HL_KEYWORDS.has(tok)) out += `<span class="tok-k">${escapeHtml(tok)}</span>`;
      else out += escapeHtml(tok);
      last = m.index + tok.length;
    }
    return out + escapeHtml(code.slice(last));
  }

  function closePreview() {
    previewSeq++;                                       // 作废所有在途回调
    if (previewAbort) { try { previewAbort.abort(); } catch (e) { /* 忽略 */ } previewAbort = null; }
    if (previewSlowTimer) { clearTimeout(previewSlowTimer); previewSlowTimer = null; }
    stopMedia();
    dropObjectUrl();
    previewPath = null;
    const m = $("#modal");
    m.classList.remove("preview");
    closeModal();
  }
  function cancelPreview() { closePreview(); }

  function dismissModal() {
    if (state.deleteBusy) return;   // 删除进行中只能点「取消」，避免误关弹层以为已停
    if ($("#modal").classList.contains("preview")) closePreview();
    else closeModal();
  }

  // ---------- 主题 ----------
  function applyTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    const btn = $("#theme-toggle");
    if (btn) btn.textContent = t === "dark" ? "☀ 亮色" : "🌙 暗色";
    localStorage.setItem("cybercat_theme", t);
  }
  function toggleTheme() {
    const next = state.theme === "dark" ? "light" : "dark";
    state.theme = next; applyTheme(next);
  }
  function initTheme() {
    const saved = localStorage.getItem("cybercat_theme") || "light";
    state.theme = saved; applyTheme(saved);
  }

  // ---------- 设置 ----------
  let settingsFields = [];

  async function openSettings() {
    if ($("#modal").classList.contains("preview")) closePreview();
    let data;
    try {
      data = await api("GET", "/api/settings");
    } catch (e) {
      toast(e.message, "err");
      return;
    }
    settingsFields = data.fields || [];
    const rows = settingsFields.map((f) => {
      const id = `set-${f.key}`;
      let input;
      if (f.type === "bool") {
        input = `<input type="checkbox" id="${id}" ${data.values[f.key] ? "checked" : ""}>`;
      } else if (f.type === "choice") {
        input = `<select id="${id}">${f.choices.map((c) =>
          `<option value="${c}" ${String(c) === String(data.values[f.key]) ? "selected" : ""}>${c}</option>`).join("")}</select>`;
      } else {
        input = `<input type="number" id="${id}" value="${data.values[f.key]}" min="${f.min}" max="${f.max}" step="1">`;
      }
      return `<div class="field set-field">
        <label for="${id}">${escapeHtml(f.label)}${f.unit ? `（${escapeHtml(f.unit)}）` : ""}</label>
        ${input}
        ${f.help ? `<div class="set-help">${escapeHtml(f.help)}</div>` : ""}
      </div>`;
    }).join("");
    const m = $("#modal");
    m.classList.remove("preview");
    m.innerHTML = `<div class="modal-card settings-card">
      <div class="modal-title">设置</div>
      <div class="modal-body settings-body">${rows}</div>
      <div class="modal-foot">
        <button class="btn outline" onclick="SFPM.closeModal()">取消</button>
        <button class="btn primary" onclick="SFPM.saveSettings()">保存</button>
      </div>
    </div>`;
    m.classList.remove("hidden");
  }

  // 启动时读设置
  async function loadSettings() {
    try {
      const j = await api("GET", "/api/settings");
      state.transcodeDefault = !!j.values.transcode_default;
    } catch (e) { /* 读不到按直连 */ }
  }

  async function saveSettings() {
    const patch = {};
    for (const f of settingsFields) {
      const el = $(`#set-${f.key}`);
      if (!el) continue;
      if (f.type === "bool") patch[f.key] = el.checked;
      else if (f.type === "choice") patch[f.key] = typeof f.choices[0] === "number" ? Number(el.value) : el.value;
      else patch[f.key] = Number(el.value);
    }
    try {
      const data = await api("PUT", "/api/settings", patch);
      state.transcodeDefault = !!data.values.transcode_default;
      settingsFields = [];
      closeModal();
      toast("设置已保存", "ok");
    } catch (e) {
      toast(e.message, "err");
    }
  }

  // ---------- 模态框 ----------
  function openModal(html) {
    const m = $("#modal");
    m.innerHTML = `<div class="modal-card">${html}</div>`;
    m.classList.remove("hidden");
  }
  function closeModal() { $("#modal").classList.add("hidden"); }

  // ---------- 工具 ----------
  function joinPath(a, b) {
    a = a.replace(/\/+$/, "");
    return a + "/" + b;
  }
  function fileIcon(name) {
    if (/\.(png|jpe?g|gif|webp|svg|ico)$/i.test(name)) return "🖼";
    if (/\.(mp4|mkv|avi|mov|webm)$/i.test(name)) return "🎬";
    if (/\.(mp3|wav|flac|aac)$/i.test(name)) return "🎵";
    if (/\.(zip|tar|gz|bz2|xz|7z|rar)$/i.test(name)) return "🗜";
    return "📄";
  }
  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function escapeAttr(s) { return escapeHtml(s); }
  function escapeAttrSingle(s) { return String(s == null ? "" : s).replace(/\\/g, "\\\\").replace(/'/g, "\\'").replace(/"/g, "&quot;"); }

  // ---------- 缩放 ----------
  function zoom(delta) {
    state.scale = Math.min(2, Math.max(0.6, state.scale + delta));
    applyScale(); saveScale();
  }
  function zoomReset() { state.scale = 1; applyScale(); saveScale(); }

  // ---------- 事件绑定 ----------
  function bind() {
    $("#btn-settings").onclick = openSettings;
    $("#btn-home").onclick = showBooks;
    $("#theme-toggle").onclick = toggleTheme;
    $("#zoom-in").onclick = () => zoom(0.1);
    $("#zoom-out").onclick = () => zoom(-0.1);
    $("#zoom-reset").onclick = zoomReset;
    document.addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === "+" || e.key === "=")) { e.preventDefault(); zoom(0.1); }
      if ((e.metaKey || e.ctrlKey) && e.key === "-") { e.preventDefault(); zoom(-0.1); }
      if ((e.metaKey || e.ctrlKey) && e.key === "0") { e.preventDefault(); zoomReset(); }
      if (e.key === "Escape") dismissModal();
    });
    // 勾选走事件委托：大目录下不必给每一行挂 handler
    $("#view-browser").addEventListener("change", (e) => {
      const cb = e.target;
      if (!cb.matches || !cb.matches(".file-check input")) return;
      const row = cb.closest(".file-row");
      if (row) toggleCheck(row.dataset.path, cb.checked);
    });
    // 点击 modal 背景关闭
    $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") dismissModal(); });
    // hash 路由变化时重新解析（支持 #/host/path 链接）
    window.addEventListener("hashchange", () => parseUrlOnLoad());
  }

  // ---------- 启动 ----------
  window.SFPM = {
    showBooks, connect, openBookForm, saveBook, delBook, testBook, syncSsh,
    goto, goUp, openDir, editPath, newDir, uploadFile, downloadOne, renameItem, deleteItem, back,
    setSort, toggleSortDir, toggleCheck, toggleCheckAll, batchDelete, batchDownload,
    scrollTopList, toggleHidden,
    previewFile, closePreview, cancelPreview, toggleVideoSource,
    openSettings, saveSettings,
    toggleTheme,
    closeModal, cancelDelete,
  };
  bind();
  initScale();
  initTheme();
  initHidden();
  loadSettings().then(() => loadBookmarks()).then(() => parseUrlOnLoad());
})();
