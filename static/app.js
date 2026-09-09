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
    hasMore: false,
    selected: null,
    loading: false,
    scale: 1,
    sortKey: "name",   // name | mtime | size
    sortDir: "asc",    // asc | desc
    theme: "light",
    checkAll: false,
    selectedSet: new Set(),  // 多选路径集合
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

  // ---------- 视图切换 ----------
  function showBooks() {
    state.view = "books";
    state.current = null;
    $("#view-books").classList.remove("hidden");
    $("#view-browser").classList.add("hidden");
    $("#btn-home").classList.add("active");
    renderBooks();
  }

  function showBrowser(bm) {
    state.view = "browser";
    state.current = bm;
    state.path = bm.default_path || "/";
    state.entries = [];
    state.selected = null;
    $("#view-books").classList.add("hidden");
    $("#view-browser").classList.remove("hidden");
    $("#btn-home").classList.remove("active");
    loadDir();
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

  async function loadDir(append) {
    if (state.loading) return;
    if (!append) { state.entries = []; state.total = 0; state.hasMore = false; }
    state.loading = true;
    const bm = state.current;
    const limit = 500;
    const path = state.path;
    try {
      const r = await api("GET", `/api/bookmarks/${bm.id}/list?path=${encodeURIComponent(path)}&limit=${limit}`);
      if (!append) {
        state.entries = r.entries;
        state.total = r.total;
        state.hasMore = r.has_more;
      } else {
        state.entries.push(...r.entries);
        state.hasMore = r.has_more;
      }
      renderBrowser();
    } catch (e) {
      if (!append) {
        $("#view-browser").innerHTML = `<div class="loading">${escapeHtml(e.message)}</div><div class="load-more"><button class="btn" onclick="SFPM.back()">返回书签</button></div>`;
      }
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

    const toolbar = `
      <div class="browser-toolbar">
        <button class="btn outline sm" onclick="SFPM.goto('${escapeAttrSingle(state.path)}')" title="刷新">⟳ 刷新</button>
        <button class="btn outline sm" onclick="SFPM.goUp()" title="上级">↑ 上级</button>
        <button class="btn outline sm" onclick="SFPM.newDir()">+ 新建目录</button>
        <button class="btn outline sm" onclick="SFPM.uploadFile()">↑ 上传</button>
        <select class="sort-select sm" title="排序字段" onchange="SFPM.setSort(this.value)">
          <option value="name" ${state.sortKey==="name"?"selected":""}>按名称</option>
          <option value="mtime" ${state.sortKey==="mtime"?"selected":""}>按时间</option>
          <option value="size" ${state.sortKey==="size"?"selected":""}>按大小</option>
        </select>
        <button class="btn outline sm" title="升序" onclick="SFPM.toggleSortDir()">${state.sortDir==="asc"?"↑":"↓"} ${state.sortDir==="asc"?"升":"降"}</button>
        <span class="count">${state.total} 项</span>
      </div>`;

    // 排序：目录永远优先；目录内部与文件内部各自按 sortKey/sortDir 排序
    const dirs = state.entries.filter(e => e.is_dir);
    const files = state.entries.filter(e => !e.is_dir);
    const cmp = (a, b) => {
      let av, bv;
      if (state.sortKey === "mtime") { av = a.mtime || 0; bv = b.mtime || 0; }
      else if (state.sortKey === "size") { av = a.size || 0; bv = b.size || 0; }
      else { av = a.name.toLowerCase(); bv = b.name.toLowerCase(); }
      if (av < bv) return -1;
      if (av > bv) return 1;
      return 0;
    };
    const sortedDirs = [...dirs].sort(cmp);
    const sortedFiles = [...files].sort(cmp);
    if (state.sortDir === "desc") { sortedDirs.reverse(); sortedFiles.reverse(); }
    const viewEntries = sortedDirs.concat(sortedFiles);

    const selCount = state.selectedSet.size;
    const batchBar = selCount > 0 ? `
      <div class="batch-bar">
        <input type="checkbox" class="check-all" ${state.checkAll?"checked":""} onchange="SFPM.toggleCheckAll(this.checked)">
        <span class="count">已选 ${selCount} 项</span>
        <div class="spacer" style="flex:1"></div>
        <button class="btn outline sm" onclick="SFPM.batchDownload()">批量下载</button>
        <button class="btn danger sm" onclick="SFPM.batchDelete()">批量删除</button>
      </div>` : "";

    let listHtml;
    if (state.entries.length === 0 && !state.loading) {
      listHtml = `<div class="loading">（空目录或还未加载）</div>`;
    } else {
      const rows = viewEntries.map((e) => {
        const checked = state.selectedSet.has(e.path) ? "checked" : "";
        return `
        <div class="file-row ${checked?"selected":""}" data-path="${escapeAttr(e.path)}" data-dir="${e.is_dir}">
          <div class="file-check"><input type="checkbox" ${checked} onchange="event.stopPropagation();SFPM.toggleCheck('${escapeAttrSingle(e.path)}',this.checked)"></div>
          <div class="file-icon">${e.is_dir ? "📁" : fileIcon(e.name)}</div>
          <div class="file-name ${e.is_dir ? "dir" : ""}" ${e.is_dir ? `ondblclick="SFPM.openDir('${escapeAttrSingle(e.path)}')" onclick="SFPM.selectRow(this)"` : `ondblclick="SFPM.downloadOne('${escapeAttrSingle(e.path)}')" `}>${escapeHtml(e.name)}</div>
          <div class="file-size">${e.is_dir ? "-" : fmtSize(e.size)}</div>
          <div class="file-mtime">${fmtTime(e.mtime)}</div>
          <div class="file-ops">
            <button class="btn sm" title="下载" onclick="event.stopPropagation();SFPM.downloadOne('${escapeAttrSingle(e.path)}')">↓</button>
            <button class="btn sm" title="重命名" onclick="event.stopPropagation();SFPM.renameItem('${escapeAttrSingle(e.path)}')">✎</button>
            <button class="btn danger sm" title="删除" onclick="event.stopPropagation();SFPM.deleteItem('${escapeAttrSingle(e.path)}')">🗑</button>
          </div>
        </div>`;
      }).join("");
      listHtml = `<div class="file-list">${rows}</div>`;
    }

    const loadMore = state.hasMore
      ? `<div class="load-more"><button class="btn outline" onclick="SFPM.loadMore()">加载更多…</button></div>`
      : "";

    $("#view-browser").innerHTML = `
      <div class="crumbs">${crumbHtml}</div>
      ${toolbar}
      ${batchBar}
      ${listHtml}
      ${loadMore}
    `;
  }

  function goto(path) {
    state.path = path; state.selected = null; loadDir();
  }

  function goUp() {
    const parts = state.path.replace(/\/+$/, "").split("/");
    parts.pop();
    goto(parts.join("/") || "/");
  }

  function loadMore() { loadDir(true); }

  function openDir(path) { goto(path); }

  function selectRow(row) {
    $$(".file-row").forEach((r) => r.classList.remove("selected"));
    row.classList.add("selected");
    state.selected = row.dataset.path;
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

  async function deleteItem(path) {
    if (!confirm(`删除 ${path.split("/").pop()}？`)) return;
    try {
      await api("POST", `/api/bookmarks/${state.current.id}/delete?path=${encodeURIComponent(path)}`);
      toast("已删除", "ok");
      loadDir();
    } catch (e) { toast(e.message, "err"); }
  }

  function back() { showBooks(); }

  // ---------- 排序 ----------
  function setSort(key) { state.sortKey = key; renderBrowser(); }
  function toggleSortDir() { state.sortDir = state.sortDir === "asc" ? "desc" : "asc"; renderBrowser(); }

  // ---------- 多选 ----------
  function toggleCheck(path, checked) {
    if (checked) state.selectedSet.add(path);
    else state.selectedSet.delete(path);
    state.checkAll = state.selectedSet.size === state.entries.filter(e => e.is_dir || !e.is_dir).length && state.entries.length > 0;
    renderBrowser();
  }
  function toggleCheckAll(checked) {
    state.selectedSet.clear();
    if (checked) state.entries.forEach(e => state.selectedSet.add(e.path));
    state.checkAll = checked;
    renderBrowser();
  }
  function selectedPaths() {
    // 保持与当前排序一致：取 entries 中在 selectedSet 里的，目录优先
    const all = state.entries.filter(e => state.selectedSet.has(e.path));
    return all.map(e => e.path);
  }
  async function batchDelete() {
    const paths = selectedPaths();
    if (paths.length === 0) return;
    if (!confirm(`删除选中的 ${paths.length} 项？`)) return;
    for (const p of paths) {
      try { await api("POST", `/api/bookmarks/${state.current.id}/delete?path=${encodeURIComponent(p)}`); }
      catch (e) { toast(`删除 ${p} 失败: ${e.message}`, "err"); }
    }
    state.selectedSet.clear(); state.checkAll = false;
    toast(`已删除 ${paths.length} 项`, "ok");
    loadDir();
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
    $("#btn-home").onclick = showBooks;
    $("#theme-toggle").onclick = toggleTheme;
    $("#zoom-in").onclick = () => zoom(0.1);
    $("#zoom-out").onclick = () => zoom(-0.1);
    $("#zoom-reset").onclick = zoomReset;
    document.addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === "+" || e.key === "=")) { e.preventDefault(); zoom(0.1); }
      if ((e.metaKey || e.ctrlKey) && e.key === "-") { e.preventDefault(); zoom(-0.1); }
      if ((e.metaKey || e.ctrlKey) && e.key === "0") { e.preventDefault(); zoomReset(); }
      if (e.key === "Escape") closeModal();
    });
    // 点击 modal 背景关闭
    $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
  }

  // ---------- 启动 ----------
  window.SFPM = {
    showBooks, connect, openBookForm, saveBook, delBook, testBook, syncSsh,
    goto, goUp, loadMore, openDir, selectRow, newDir, uploadFile, downloadOne, renameItem, deleteItem, back,
    setSort, toggleSortDir, toggleCheck, toggleCheckAll, batchDelete, batchDownload,
    toggleTheme,
    closeModal,
  };
  bind();
  initScale();
  initTheme();
  loadBookmarks();
})();
