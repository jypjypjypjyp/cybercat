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
        <div class="books-title">我的书签</div>
        <button class="btn primary" onclick="SFPM.openBookForm()">+ 新建书签</button>
      </div>`;
    let grid;
    if (state.bookmarks.length === 0) {
      grid = `<div class="empty-state">还没有书签，点「新建书签」开始。</div>`;
    } else {
      grid = `<div class="book-grid">` + state.bookmarks.map((b) => `
        <div class="book-card">
          <div class="book-name">${escapeHtml(b.name || b.host)}</div>
          <div class="book-meta">${escapeHtml(b.username)}@${escapeHtml(b.host)}:${b.port}</div>
          <div class="book-actions">
            <button class="btn primary sm" onclick="SFPM.connect('${b.id}')">连接</button>
            <button class="btn sm" onclick="SFPM.openBookForm('${b.id}')">编辑</button>
            <button class="btn sm" onclick="SFPM.testBook('${b.id}')">测试</button>
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

  // ---------- 文件浏览器 ----------
  async function connect(id) {
    toast("连接中...");
    try {
      const bm = state.bookmarks.find((x) => x.id === id);
      await api("POST", `/api/bookmarks/${id}/connect`);
      showBrowser(bm);
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
        <button class="btn sm" onclick="SFPM.goto('${escapeAttrSingle(state.path)}')" title="刷新">⟳ 刷新</button>
        <button class="btn sm" onclick="SFPM.goUp()" title="上级">↑</button>
        <button class="btn sm" onclick="SFPM.newDir()">+ 新建目录</button>
        <button class="btn sm" onclick="SFPM.uploadFile()">↑ 上传</button>
        <div class="spacer" style="flex:1"></div>
        <span class="file-mtime" style="text-align:right">${state.total} 项</span>
      </div>`;

    let listHtml;
    if (state.entries.length === 0 && !state.loading) {
      listHtml = `<div class="loading">（空目录或还未加载）</div>`;
    } else {
      listHtml = `<div class="file-list">` + state.entries.map((e) => `
        <div class="file-row" data-path="${escapeAttr(e.path)}" data-dir="${e.is_dir}" onclick="SFPM.selectRow(this)">
          <div class="file-icon">${e.is_dir ? "📁" : fileIcon(e.name)}</div>
          <div class="file-name ${e.is_dir ? "dir" : ""}" ${e.is_dir ? `ondblclick="SFPM.openDir('${escapeAttrSingle(e.path)}')"` : `ondblclick="SFPM.downloadOne('${escapeAttrSingle(e.path)}')"`}>${escapeHtml(e.name)}</div>
          <div class="file-size">${e.is_dir ? "-" : fmtSize(e.size)}</div>
          <div class="file-mtime">${fmtTime(e.mtime)}</div>
          <div class="file-ops">
            <button class="btn sm" title="下载" onclick="event.stopPropagation();SFPM.downloadOne('${escapeAttrSingle(e.path)}')">↓</button>
            <button class="btn sm" title="重命名" onclick="event.stopPropagation();SFPM.renameItem('${escapeAttrSingle(e.path)}')">✎</button>
            <button class="btn danger sm" title="删除" onclick="event.stopPropagation();SFPM.deleteItem('${escapeAttrSingle(e.path)}')">🗑</button>
          </div>
        </div>`).join("") + `</div>`;
    }

    const loadMore = state.hasMore
      ? `<div class="load-more"><button class="btn" onclick="SFPM.loadMore()">加载更多…</button></div>`
      : "";

    $("#view-browser").innerHTML = `
      <div class="crumbs">${crumbHtml}</div>
      ${toolbar}
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
    showBooks, connect, openBookForm, saveBook, delBook, testBook,
    goto, goUp, loadMore, openDir, selectRow, newDir, uploadFile, downloadOne, renameItem, deleteItem, back,
    closeModal,
  };
  bind();
  initScale();
  loadBookmarks();
})();
