/* 管理员页逻辑：只做"把带 require_admin 的现成接口变成可点的按钮"这一件事。
 *
 * 四条硬规矩：
 * 1) 一切数据都来自带 require_admin 的接口。页面本身是静态空壳，任何人打开都
 *    是一个空表格（由 test_admin_page.py 钉住）。
 * 2) 用户名与任务目标都是别人写下的字符串，一律走 textContent / createElementNS
 *    建树，绝不拼 innerHTML——拼进 HTML 等于让某个人的用户名在这台管理员的
 *    浏览器里执行。
 * 3) 换发回来的令牌明文只存在于内存和这一次弹窗里，不写 localStorage、不打控制台。
 * 4) 界面上每句话都说后端真做了的事。删除与换发到底动了哪三份数据，读的是
 *    app/core/auth.py 里 delete_user / rotate_token 的实现，不是想象。
 */
(() => {
  "use strict";

  const KEY = "accessToken";                 // 与 /app 同一个键：同源，登录态共享
  const $ = (id) => document.getElementById(id);
  let token = localStorage.getItem(KEY) || "";

  /* ---------- 建树 ---------- */

  const SVGNS = "http://www.w3.org/2000/svg";
  // 线性图标，与 PWA 设置页那批同一口径（24 视图、1.7 描边、currentColor 上色）。
  const ICONS = {
    users:   ["M4 20v-1.4A4.6 4.6 0 0 1 8.6 14h2.8A4.6 4.6 0 0 1 16 18.6V20",
              "M9.3 11.3a3.4 3.4 0 1 0 0-6.8 3.4 3.4 0 0 0 0 6.8Z",
              "M17 14.2a4.6 4.6 0 0 1 3 4.4V20"],
    memory:  ["M12 4c3.9 0 7 1.3 7 2.9S15.9 9.8 12 9.8 5 8.5 5 6.9 8.1 4 12 4Z",
              "M5 6.9v10.2C5 18.7 8.1 20 12 20s7-1.3 7-2.9V6.9",
              "M5 12c0 1.6 3.1 2.9 7 2.9s7-1.3 7-2.9"],
    tasks:   ["M9 6h11M9 12h11M9 18h11", "M4.5 6h.01M4.5 12h.01M4.5 18h.01"],
    agent:   ["M12 3.5l2.1 4.6 4.6 2.1-4.6 2.1L12 16.9l-2.1-4.6L5.3 10.2l4.6-2.1L12 3.5Z",
              "M18.5 16.5l.9 1.9 1.9.9-1.9.9-.9 1.9-.9-1.9-1.9-.9 1.9-.9.9-1.9Z"],
    refresh: ["M20 12a8 8 0 1 1-2.6-5.9", "M20 4v4h-4"],
    exit:    ["M14 4h5v16h-5", "M10 12H3", "M6 9l-3 3 3 3"],
    login:   ["M10 4H5v16h5", "M14 12h7", "M18 9l3 3-3 3"],
    power:   ["M12 4v8", "M7.5 6.8a7 7 0 1 0 9 0"],
    key:     ["M8.4 15.6a3.6 3.6 0 1 0 0-7.2 3.6 3.6 0 0 0 0 7.2Z",
              "M11 10.4L20 10l-.6 3.4 2-2-2.6.4", "M13.6 12.9l4.6 4.6"],
    trash:   ["M6 7h12", "M9.5 7V4.8h5V7", "M7.5 7l.9 13h7.2l.9-13", "M11 11v6M13 11v6"],
    copy:    ["M9 9h11v11H9z", "M15 9V4H4v11h5"],
    check:   ["M5 12.5l4.5 4.5L19 7"],
    play:    ["M8 5.5l11 6.5-11 6.5z"],
    stop:    ["M7 7h10v10H7z"],
    eye:     ["M2.5 12S6 6.5 12 6.5 21.5 12 21.5 12 18 17.5 12 17.5 2.5 12 2.5 12Z",
              "M12 14.4a2.4 2.4 0 1 0 0-4.8 2.4 2.4 0 0 0 0 4.8Z"],
    decay:   ["M12 5v9", "M8 10.5l4 4 4-4", "M5 19h14"],
    // 用量：三根柱子。与 PWA 那套同一口径（24 视图、1.7 描边、currentColor 上色）
    usage:   ["M6 20V11", "M12 20V4.5", "M18 20v-6"],
    close:   ["M6 6l12 12M18 6L6 18"],
  };

  function icon(name) {
    const svg = document.createElementNS(SVGNS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "1.7");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    (ICONS[name] || []).forEach((d) => {
      const p = document.createElementNS(SVGNS, "path");
      p.setAttribute("d", d);
      svg.appendChild(p);
    });
    return svg;
  }

  function cell(tag, text, cls) {
    const el = document.createElement(tag);
    if (text !== undefined && text !== null) el.textContent = text;
    if (cls) el.className = cls;
    return el;
  }

  function iconWrap(name) {
    const wrap = cell("span", undefined, "ico");
    wrap.appendChild(icon(name));
    return wrap;
  }

  function labelSpan(text) { return cell("span", text, "lbl"); }

  function btn(iconName, label, cls, fn) {
    const b = cell("button", undefined, cls);
    b.type = "button";
    b.appendChild(iconWrap(iconName));
    b.appendChild(labelSpan(label));
    if (fn) b.addEventListener("click", fn);
    return b;
  }

  // 图标 + 文字：静态按钮把图标名与文字写在 data-icon / data-label 上，建树时统一
  // 补上，省得在 HTML 里手抄几十行 path。
  function decorateIcons(root) {
    (root || document).querySelectorAll("[data-icon]").forEach((el) => {
      if (el.firstElementChild) return;
      el.appendChild(iconWrap(el.getAttribute("data-icon")));
      const text = el.getAttribute("data-label");
      if (text) el.appendChild(labelSpan(text));
    });
  }

  /* ---------- 请求 ---------- */

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
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data || {};
  }

  // 每个按钮同一套：按下先禁用（一次点击只允许一个动作），失败就把原因写在顶上的红字里。
  async function busy(el, fn) {
    if (el) { el.disabled = true; }
    try { await fn(); }
    catch (e) { if (e.message !== "401") flash(e.message, true); }
    finally { if (el) el.disabled = false; }
  }

  /* ---------- 提示与弹层 ---------- */

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

  function fmtTime(iso) {
    // 后端存的是带微秒的 ISO；直接显示既难看，也和"只精确到小时"的说明打脸
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    const p = (n) => String(n).padStart(2, "0");
    return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  let askResolve = null;
  function ask(title, body, okLabel, iconName, danger) {
    $("askTitle").textContent = title;
    $("askBody").textContent = body;
    const ok = $("askOk");
    ok.textContent = "";
    ok.className = "btn btn-small " + (danger ? "danger-btn" : "btn-primary");
    ok.appendChild(iconWrap(iconName || "check"));
    ok.appendChild(labelSpan(okLabel));
    $("ask").classList.remove("hidden");
    return new Promise((resolve) => { askResolve = resolve; });
  }
  function closeAsk(answer) {
    $("ask").classList.add("hidden");
    const r = askResolve;
    askResolve = null;
    if (r) r(answer);
  }

  function showDetail(title, value) {
    $("detailTitle").textContent = title;
    $("detailText").textContent = typeof value === "string"
      ? value : JSON.stringify(value, null, 2);
    $("detail").classList.remove("hidden");
  }

  function reveal(secret) {
    $("revealText").textContent = secret;
    $("reveal").classList.remove("hidden");
  }

  /* ---------- 分区切换 ---------- */

  function showSection(id) {
    document.querySelectorAll(".sec").forEach((s) => {
      s.classList.toggle("hidden", s.id !== id);
    });
    document.querySelectorAll(".tab").forEach((t) => {
      t.classList.toggle("active", t.getAttribute("data-goto") === id);
    });
  }

  /* ---------- 用户 ---------- */

  // user_id → 用户名。用量那一节要把账本上的 id 翻成看得懂的人名，读的就是这张表。
  // 只有 loadUsers 写它：别处再 fetch 一次 /v1/admin/users 就是第二个真相。
  let nameById = {};

  async function loadUsers() {
    const { users } = await req("/v1/admin/users");
    nameById = {};
    (users || []).forEach((u) => { nameById[u.user_id] = u.username; });
    const tbody = $("userRows");
    tbody.textContent = "";
    if (!users || !users.length) {
      tbody.appendChild(emptyRow("还没有注册用户。把地址发给谁，他自己填用户名和密码就能用。", 7));
      return;
    }
    users.forEach((u) => tbody.appendChild(userRow(u)));
  }

  function userRow(u) {
    const name = u.username;
    const uid = encodeURIComponent(u.user_id);
    const tr = document.createElement("tr");

    tr.appendChild(cell("td", name, "name"));
    tr.appendChild(cell("td", u.role === "admin" ? "管理员" : "用户"));
    const st = cell("td");
    st.appendChild(cell("span", u.disabled ? "已停用" : "正常", "tag" + (u.disabled ? " off" : "")));
    tr.appendChild(st);
    tr.appendChild(cell("td", String(u.sessions ?? "—")));
    tr.appendChild(cell("td", fmtTime(u.last_seen)));
    // /v1/admin/users 只报令牌数，记忆按人几条后端不给；这里不猜、不算，直接说不支持。
    tr.appendChild(cell("td", "不支持", "dim"));

    const acts = cell("td", undefined, "acts");
    acts.appendChild(btn(u.disabled ? "power" : "stop", u.disabled ? "恢复" : "停用",
      "btn btn-small btn-ghost", (e) => busy(e.currentTarget, () => toggleUser(u))));
    acts.appendChild(btn("key", "换发令牌", "btn btn-small btn-ghost",
      (e) => busy(e.currentTarget, () => rotate(u))));
    acts.appendChild(btn("trash", "删号", "btn btn-small btn-ghost",
      (e) => busy(e.currentTarget, () => remove(u))));
    tr.appendChild(acts);
    return tr;
  }

  async function toggleUser(u) {
    const verb = u.disabled ? "恢复" : "停用";
    const ok = await ask(
      verb + "用户「" + u.username + "」？",
      u.disabled
        ? "只把这一条身份记录上的停用标记撤掉，他的设备令牌还是刚才那批：原来登录着的设备接着用，中途掉线的重新登录就行。"
        : "只在那一条身份记录上打一个停用标记：他名下每一枚会话令牌当场不再被认，每一台设备都掉线，密码也登不进来。记录、会话、附件、记忆一个字都不动，随时可以恢复。",
      verb, "power", !u.disabled);
    if (!ok) return;
    await req("/v1/admin/users/" + encodeURIComponent(u.user_id)
              + (u.disabled ? "/enable" : "/disable"), { method: "POST" });
    flash("已" + verb);
    await loadUsers();
  }

  async function rotate(u) {
    const ok = await ask(
      "给「" + u.username + "」换发新令牌？",
      "他名下现有的会话令牌会全部作废，每一台设备都掉线；新令牌的明文只出现在这一次响应里。这一步不会解除停用状态。注意它不是把门焊死：他知道自己密码的话，重新登录就能再领一枚——要挡住人请用停用。",
      "换发", "key", false);
    if (!ok) return;
    const r = await req("/v1/admin/users/" + encodeURIComponent(u.user_id)
                        + "/rotate-token", { method: "POST" });
    if (r.token) reveal(r.token);
    await loadUsers();
  }

  async function remove(u) {
    // 这段文案对着 app/core/auth.py 的 delete_user 写：它只 del 掉 users 里的这一条
    // 记录（令牌存在记录里，所以一起没了），既不碰 sessions.json，也不碰附件目录和
    // 向量库。原来说"记录与令牌一起没了"，把留下的那三份说成也删了。
    const ok = await ask(
      "删除用户「" + u.username + "」？",
      "删的只有这一条身份记录。令牌就存在这条记录里，所以他的令牌会跟着一起失效，每一台设备掉线，而且没有恢复入口。"
      + "他的会话、附件、长期记忆不在这次删除里：那三份各存各的，删号之后原样留在磁盘上，只是那个 user_id 再也解析不出身份——除了手工改数据文件，没有第二条路能清掉它们。",
      "确认删除", "trash", true);
    if (!ok) return;
    await req("/v1/admin/users/" + encodeURIComponent(u.user_id), { method: "DELETE" });
    flash("已删除（他的会话、附件、记忆还在磁盘上）");
    await Promise.all([loadUsers(), loadMemory()]);
  }

  function emptyRow(text, span) {
    const tr = document.createElement("tr");
    const td = cell("td", text);
    td.colSpan = span;
    td.style.color = "var(--text-3)";
    tr.appendChild(td);
    return tr;
  }

  /* ---------- 用量 ---------- */

  // 账本上 paid_by 只有这两个取值（providers.py 里是白名单硬校验）。这一栏要回答的是
  // "谁的钱"，所以不把 operator / user 原样扔给看页面的人。
  const PAID = { operator: "服务端垫的", user: "他自带的 key" };

  let usageDay = "";                         // 选择器当前看的那一天，空 = 让后端给今天

  function fmtN(n) {
    // 不交给 toLocaleString：各家 WebView 的默认分组规则不一样，同一个数会显示成两种样子
    const s = String(Math.abs(Math.round(n || 0)));
    return (n < 0 ? "-" : "") + s.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  async function loadUsage() {
    const sel = $("usageDay");
    const data = await req(usageDay
      ? "/v1/admin/usage?day=" + encodeURIComponent(usageDay)
      : "/v1/admin/usage");
    // 哪天是"正在看的这天"由后端回回来，本地不猜：账本按本地时区切天，浏览器时区
    // 跟服务端不一致时（手机在别的网络里）自己拼今天会问到另一天去。
    usageDay = data.day || "";

    const days = (data.days || []).slice();
    if (usageDay && days.indexOf(usageDay) < 0) days.push(usageDay);
    days.sort().reverse();                   // YYYY-MM-DD 字典序就是时间序
    sel.textContent = "";
    days.forEach((d) => {
      const o = cell("option", d);
      o.value = d;
      sel.appendChild(o);
    });
    sel.value = usageDay;

    renderTotals(data.totals || {});

    const tbody = $("usageRows");
    tbody.textContent = "";
    const rows = data.rows || [];
    if (!rows.length) {
      tbody.appendChild(emptyRow("这一天没有账。上面两张卡是零，不是读不到。", 8));
      return;
    }
    rows.forEach((r) => tbody.appendChild(usageRow(r)));
  }

  function renderTotals(totals) {
    const box = $("usageTotals");
    box.textContent = "";
    Object.keys(totals).forEach((paid) => {
      const t = totals[paid] || {};
      const card = cell("div", undefined, "card grow");
      const stat = cell("div", undefined, "stat");
      stat.appendChild(cell("b", fmtN(t.total_tokens)));
      stat.appendChild(cell("span", "token · " + (PAID[paid] || paid)));
      card.appendChild(stat);
      card.appendChild(cell("p", fmtN(t.calls) + " 次 · 成功 " + fmtN(t.ok)
        + " / 失败 " + fmtN(t.failed) + " · 入 " + fmtN(t.prompt_tokens)
        + " 出 " + fmtN(t.completion_tokens), "pane-note"));
      const extra = [];
      if (t.reasoning_tokens) extra.push("推理 " + fmtN(t.reasoning_tokens));
      if (t.cached_tokens) extra.push("缓存命中 " + fmtN(t.cached_tokens));
      if (extra.length) card.appendChild(cell("p", extra.join(" · "), "pane-note"));
      if (t.unknown_usage > 0) {
        card.appendChild(cell("p", "其中 " + fmtN(t.unknown_usage)
          + " 次上游没回 token 数：这栏是下限，不是准确数", "pane-note warn"));
      }
      box.appendChild(card);
    });
  }

  function usageRow(r) {
    const tr = document.createElement("tr");
    const who = cell("td", undefined, "name");
    who.appendChild(cell("span", nameById[r.user_id] || r.user_id));
    // 删过号的人、以及 bootstrap 口令那个 default_user 都不在用户表里：
    // 账还在，但翻不出名字，就照原样把 id 摆出来。
    if (!nameById[r.user_id]) who.appendChild(cell("span", "（不在用户表里）", "dim"));
    tr.appendChild(who);
    tr.appendChild(cell("td", r.provider_id, "code"));
    tr.appendChild(cell("td", PAID[r.paid_by] || r.paid_by));
    tr.appendChild(cell("td", fmtN(r.calls)));
    tr.appendChild(cell("td", fmtN(r.ok) + " / " + fmtN(r.failed)));
    tr.appendChild(cell("td", fmtN(r.total_tokens)));
    tr.appendChild(cell("td", fmtN(r.tool_rounds)));
    const note = cell("td");
    note.appendChild(r.unknown_usage > 0
      ? cell("span", fmtN(r.unknown_usage) + " 次没回 token 数", "tag warn")
      : cell("span", "齐", "dim"));
    tr.appendChild(note);
    return tr;
  }

  /* ---------- 记忆 ---------- */

  async function loadMemory() {
    let stats;
    try {
      stats = await req("/v1/memory/stats");
    } catch (e) {
      if (e.message === "401") throw e;
      $("memTotal").textContent = "读不到";
      $("memNote").textContent = "条 · " + e.message;
      $("memCollection").textContent = "";
      return;
    }
    $("memTotal").textContent = String(stats.total_memories ?? "—");
    $("memNote").textContent = "条 · 全库合计（不分人）";
    $("memCollection").textContent = "集合：" + (stats.collection_name || "—");
  }

  async function decay() {
    const factor = $("decayFactor").value || "0.95";
    const ok = await ask(
      "把你自己这池记忆的权重乘 " + factor + "？",
      "作用对象是登录这个页面的这个人——也就是你自己的记忆池，不是列表里任何别人的。整池一起压低，压完不会自己涨回来。",
      "衰减", "decay", true);
    if (!ok) return;
    await req("/v1/memory/decay?decay_factor=" + encodeURIComponent(factor), { method: "POST" });
    flash("已衰减你自己的记忆权重");
    await loadMemory();
  }

  /* ---------- 任务 ---------- */

  async function loadTasks() {
    const box = $("taskList");
    box.textContent = "";
    const data = await req("/v1/tasks");
    const tasks = data.tasks || [];
    if (!tasks.length) {
      box.appendChild(cell("p", "任务表是空的。这条进程起来之后没跑过编排。", "pane-note"));
      return;
    }
    tasks.forEach((t) => box.appendChild(taskRow(t)));
  }

  function taskRow(t) {
    const row = cell("div", undefined, "task");
    const meta = cell("div", undefined, "tm");
    meta.appendChild(cell("b", t.goal));
    meta.appendChild(cell("span", t.task_id + " · " + fmtTime(t.created_at)));
    row.appendChild(meta);
    row.appendChild(cell("span", (t.status || "—") + " " + (t.progress || ""), "st"
      + (t.status === "running" || t.status === "pending" ? " run" : "")
      + (t.status === "failed" ? " bad" : "")));

    const ops = cell("div", undefined, "ops");
    ops.appendChild(btn("eye", "详情", "btn btn-small btn-ghost",
      (e) => busy(e.currentTarget, async () => showDetail("任务 " + t.task_id,
        await req("/v1/tasks/" + encodeURIComponent(t.task_id))))));
    ops.appendChild(btn("stop", "取消", "btn btn-small btn-ghost",
      (e) => busy(e.currentTarget, async () => {
        const yes = await ask("取消任务「" + (t.goal || t.task_id) + "」？",
          "只是打个取消标记：当前那个子任务会跑完，然后停下来。已经在跑的上游调用退不回钱。",
          "取消任务", "stop", true);
        if (!yes) return;
        const r = await req("/v1/tasks/" + encodeURIComponent(t.task_id) + "/cancel", { method: "POST" });
        flash(r.message || "已标记取消");
        await loadTasks();
      })));
    ops.appendChild(btn("trash", "移出", "btn btn-small btn-ghost",
      (e) => busy(e.currentTarget, async () => {
        const yes = await ask("把任务 " + t.task_id + " 移出这张表？",
          "删的是进程内这一条记录：结果、子任务、最终答案从这里消失。它本来也不落盘，重启即空。",
          "移出", "trash", true);
        if (!yes) return;
        await req("/v1/tasks/" + encodeURIComponent(t.task_id), { method: "DELETE" });
        flash("已移出");
        await loadTasks();
      })));
    row.appendChild(ops);
    return row;
  }

  /* ---------- 智能体 ---------- */

  function agentBody() {
    return {
      task: $("agentInput").value.trim(),
      max_turns: Number($("agentTurns").value) || 10,
      max_duration: Number($("agentDuration").value) || 120,
    };
  }

  async function runAgent() {
    const body = agentBody();
    if (!body.task) { flash("先写要跑的事", true); return; }
    const out = $("agentOut");
    out.classList.remove("hidden");
    out.textContent = "在跑……这两条都是同步接口，服务端跑完才回话。";
    const r = await req("/v1/agent/run", { method: "POST", body });
    out.textContent = typeof r.result === "string" ? r.result : JSON.stringify(r, null, 2);
    flash("ReAct 跑完了");
  }

  async function orchestrate() {
    const body = { goal: agentBody().task };
    if (!body.goal) { flash("先写目标", true); return; }
    const out = $("agentOut");
    out.classList.remove("hidden");
    out.textContent = "在编排……跑完才回话，落进「任务」那一节的表里。";
    const r = await req("/v1/agent/orchestrate", { method: "POST", body });
    out.textContent = JSON.stringify(r, null, 2);
    flash("编排结束");
    await loadTasks();
  }

  /* ---------- 装配 ---------- */

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
    await refresh();
  }

  async function refresh() {
    await loadUsers();          // 用量那一节的「谁」要读它填的名字表，所以不能并到下一行里
    await Promise.all([loadMemory(), loadTasks(), loadUsage()]);
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
    $("panel").classList.add("hidden");
    showGate("已退出本机保存的口令（对方的令牌不受影响）。");
  });

  $("btnReload").addEventListener("click", (e) => busy(e.currentTarget, boot));

  $("tabs").addEventListener("click", (e) => {
    const tab = e.target.closest(".tab");
    if (tab) showSection(tab.getAttribute("data-goto"));
  });

  $("askOk").addEventListener("click", () => closeAsk(true));
  $("askCancel").addEventListener("click", () => closeAsk(false));
  $("ask").addEventListener("click", (e) => { if (e.target === $("ask")) closeAsk(false); });
  $("detail").addEventListener("click", (e) => { if (e.target === $("detail")) $("detail").classList.add("hidden"); });
  $("btnCloseDetail").addEventListener("click", () => $("detail").classList.add("hidden"));

  $("btnDecay").addEventListener("click", (e) => busy(e.currentTarget, decay));
  // 换日期就重新读一次：这本账一天一答，前端不按人细算，也不做"切天不重新请求"的乐观更新。
  $("usageDay").addEventListener("change", (e) => {
    usageDay = e.target.value;
    busy(null, loadUsage);
  });
  $("btnUsage").addEventListener("click", (e) => busy(e.currentTarget, loadUsage));
  $("btnRunAgent").addEventListener("click", (e) => busy(e.currentTarget, runAgent));
  $("btnOrchestrate").addEventListener("click", (e) => busy(e.currentTarget, orchestrate));

  $("btnCloseReveal").addEventListener("click", () => {
    $("reveal").classList.add("hidden");
    $("revealText").textContent = "";
  });

  $("btnCopy").addEventListener("click", async () => {
    const text = $("revealText").textContent;
    try { await navigator.clipboard.writeText(text); flash("已复制到剪贴板"); }
    catch (e) { flash("复制失败，请长按选中手动复制", true); }
  });

  decorateIcons(document);
  boot().catch((e) => { if (e.message !== "401") flash(e.message, true); });
})();
