/* 管理员页逻辑：只做"把 /v1/admin/* 现成接口变成可点的按钮"这一件事。
 *
 * 三条硬规矩：
 * 1) 一切数据都来自带 require_admin 的接口。页面本身是静态空壳，任何人打开都
 *    是一个空表格（由 test_admin_page.py 钉住）。
 * 2) 用户名等字段一律走 textContent 建树，绝不拼 innerHTML——用户名是注册时
 *    由用户自己填的，拼进 HTML 等于让某个人的用户名在这台管理员的浏览器里执行。
 * 3) 换发回来的令牌明文只存在于内存和这一次弹窗里，不写 localStorage、不打控制台。
 */
(() => {
  "use strict";

  const KEY = "accessToken";                 // 与 /app 同一个键：同源，登录态共享
  const $ = (id) => document.getElementById(id);
  let token = localStorage.getItem(KEY) || "";

  async function req(path, opts) {
    const headers = { "Content-Type": "application/json" };
    if (token) headers["Authorization"] = "Bearer " + token;
    const res = await fetch(path, {
      method: (opts && opts.method) || "GET",
      headers,
      body: opts && opts.body ? JSON.stringify(opts.body) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch (e) { /* 空响应体 */ }
    if (res.status === 401) { showGate("口令不对或已失效"); throw new Error("401"); }
    if (!res.ok) {
      const detail = data && data.detail ? data.detail : ("HTTP " + res.status);
      throw new Error(detail);
    }
    return data || {};
  }

  function showGate(msg) {
    $("panel").classList.add("hidden");
    $("gate").classList.remove("hidden");
    $("gateErr").textContent = msg || "";
  }

  function flash(msg, bad) {
    const el = $("flash");
    el.textContent = msg || "";
    el.style.color = bad ? "var(--danger)" : "var(--accent)";
  }

  function cell(tag, text, cls) {
    const el = document.createElement(tag);
    if (text !== undefined && text !== null) el.textContent = text;
    if (cls) el.className = cls;
    return el;
  }

  function fmtTime(iso) {
    // 后端存的是带微秒的 ISO；直接显示既难看，也和"只精确到小时"的说明打脸
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    const p = (n) => String(n).padStart(2, "0");
    return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function actionBtn(label, cls, fn) {
    const b = cell("button", label, "btn btn-small " + cls);
    b.type = "button";
    b.addEventListener("click", fn);
    return b;
  }

  async function boot() {
    if (!token) return showGate("");
    let me;
    try {
      me = await req("/v1/auth/me");
    } catch (e) {
      return;                                   // showGate 已在 401 分支里做过
    }
    if (me.role !== "admin") return showGate("这把口令有效，但它不是管理员。");
    $("gate").classList.add("hidden");
    $("panel").classList.remove("hidden");
    $("who").textContent = "当前身份：" + me.username + "（" + me.user_id + "）";
    await Promise.all([loadInvites(), loadUsers()]);
  }

  async function loadInvites() {
    const { invites } = await req("/v1/admin/invites");
    const tbody = $("inviteRows");
    tbody.textContent = "";
    if (!invites || !invites.length) {
      tbody.appendChild(emptyRow("还没有邀请码。点上面「生成一枚」。", 5));
      return;
    }
    const now = Date.now() / 1000;
    invites.forEach((inv) => {
      const used = (inv.used_by || []).length;
      const expired = inv.expires_at && inv.expires_at < now;
      const tr = document.createElement("tr");
      tr.appendChild(cell("td", inv.code, "code"));
      tr.appendChild(cell("td", used + " / " + inv.max_uses));
      const st = cell("td");
      st.appendChild(cell("span", expired ? "已过期" : (used >= inv.max_uses ? "已用满" : "可用"),
                          "tag" + (expired || used >= inv.max_uses ? " off" : "")));
      tr.appendChild(st);
      tr.appendChild(cell("td", inv.created_by || "—"));
      const acts = cell("td", undefined, "acts");
      acts.appendChild(actionBtn("作废", "btn-ghost", async () => {
        if (!confirm("作废邀请码 " + inv.code + "？拿到这枚码的人将立刻无法注册。")) return;
        try { await req("/v1/admin/invites/" + encodeURIComponent(inv.code), { method: "DELETE" });
              flash("已作废"); await loadInvites(); }
        catch (e) { flash(e.message, true); }
      }));
      tr.appendChild(acts);
      tbody.appendChild(tr);
    });
  }

  async function loadUsers() {
    const { users } = await req("/v1/admin/users");
    const tbody = $("userRows");
    tbody.textContent = "";
    if (!users || !users.length) {
      tbody.appendChild(emptyRow("还没有注册用户。把邀请码发给谁，谁注册后就出现在这里。", 5));
      return;
    }
    users.forEach((u) => {
      const tr = document.createElement("tr");
      tr.appendChild(cell("td", u.username));
      tr.appendChild(cell("td", u.role === "admin" ? "管理员" : "用户"));
      const st = cell("td");
      st.appendChild(cell("span", u.disabled ? "已停用" : "正常", "tag" + (u.disabled ? " off" : "")));
      tr.appendChild(st);
      tr.appendChild(cell("td", fmtTime(u.last_seen)));

      const acts = cell("td", undefined, "acts");
      acts.appendChild(actionBtn(u.disabled ? "恢复" : "停用", "btn-ghost", async () => {
        const verb = u.disabled ? "恢复" : "停用";
        if (!confirm(verb + "用户「" + u.username + "」？停用会立刻让它手上的令牌失效。")) return;
        try {
          await req("/v1/admin/users/" + encodeURIComponent(u.user_id) +
                    (u.disabled ? "/enable" : "/disable"), { method: "POST" });
          flash("已" + verb); await loadUsers();
        } catch (e) { flash(e.message, true); }
      }));
      acts.appendChild(actionBtn("换发令牌", "btn-ghost", async () => {
        if (!confirm("给「" + u.username + "」换发新令牌？旧令牌当场失效，他必须把新令牌填进 App。")) return;
        try {
          const r = await req("/v1/admin/users/" + encodeURIComponent(u.user_id) + "/rotate-token",
                              { method: "POST" });
          if (r.token) reveal(r.token);
          await loadUsers();
        } catch (e) { flash(e.message, true); }
      }));
      acts.appendChild(actionBtn("删号", "btn-ghost", async () => {
        if (!confirm("删除用户「" + u.username + "」？记录与令牌一起没了，且无法撤销。")) return;
        try {
          await req("/v1/admin/users/" + encodeURIComponent(u.user_id), { method: "DELETE" });
          flash("已删除"); await loadUsers();
        } catch (e) { flash(e.message, true); }
      }));
      tr.appendChild(acts);
      tbody.appendChild(tr);
    });
  }

  function emptyRow(text, span) {
    const tr = document.createElement("tr");
    const td = cell("td", text);
    td.colSpan = span;
    td.style.color = "var(--text-3)";
    tr.appendChild(td);
    return tr;
  }

  function reveal(secret) {
    $("revealText").textContent = secret;
    $("reveal").classList.remove("hidden");
  }

  $("btnLogin").addEventListener("click", async () => {
    const v = $("pass").value.trim();
    if (!v) return showGate("口令不能为空");
    token = v;
    localStorage.setItem(KEY, token);
    try { await boot(); }
    catch (e) { if (e.message !== "401") showGate(e.message); }
  });
  $("pass").addEventListener("keydown", (e) => { if (e.key === "Enter") $("btnLogin").click(); });

  $("btnLogout").addEventListener("click", () => {
    localStorage.removeItem(KEY);
    token = "";
    showGate("已退出本机保存的口令（对方的令牌不受影响）。");
  });

  $("btnReload").addEventListener("click", () => boot().catch(() => {}));

  $("btnMake").addEventListener("click", async () => {
    const n = Math.max(1, Math.min(50, parseInt($("maxUses").value, 10) || 1));
    try {
      const r = await req("/v1/admin/invites", { method: "POST", body: { max_uses: n } });
      const chip = $("newCode");
      chip.textContent = "新码：" + r.code;
      chip.classList.remove("hidden");
      flash("已生成。把这枚码单独发给一个人，别丢进群聊。");
      await loadInvites();
    } catch (e) { flash(e.message, true); }
  });

  $("btnCloseReveal").addEventListener("click", () => {
    $("reveal").classList.add("hidden");
    $("revealText").textContent = "";
  });

  $("btnCopy").addEventListener("click", async () => {
    const text = $("revealText").textContent;
    try { await navigator.clipboard.writeText(text); flash("已复制到剪贴板"); }
    catch (e) { flash("复制失败，请长按选中手动复制", true); }
  });

  boot().catch(() => {});
})();
