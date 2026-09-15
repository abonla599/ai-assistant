/* AI 智能助手 PWA 主逻辑 */
"use strict";

const $ = (id) => document.getElementById(id);

/* ---------------- 本地偏好 ---------------- */
const pref = {
  get model() { return localStorage.getItem("model") || "deepseek-chat"; },
  set model(v) { localStorage.setItem("model", v); },
  get sessionId() { return localStorage.getItem("sessionId") || ""; },
  set sessionId(v) { v ? localStorage.setItem("sessionId", v) : localStorage.removeItem("sessionId"); },
  get temperature() { return Number(localStorage.getItem("temperature") || 0.7); },
  set temperature(v) { localStorage.setItem("temperature", String(v)); },
  get contextWindow() { return Number(localStorage.getItem("contextWindow") || 10); },
  set contextWindow(v) { localStorage.setItem("contextWindow", String(v)); },
  get theme() {
    const saved = localStorage.getItem("theme");
    if (saved) return saved;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  },
  set theme(v) { localStorage.setItem("theme", v); },
  get userId() {
    let id = localStorage.getItem("userId");
    if (!id) { id = "web_" + Math.random().toString(36).slice(2, 10); localStorage.setItem("userId", id); }
    return id;
  },
  persona(sessionId) { return localStorage.getItem("persona:" + sessionId) || ""; },
  setPersona(sessionId, text) {
    text ? localStorage.setItem("persona:" + sessionId, text) : localStorage.removeItem("persona:" + sessionId);
  },
};

const state = {
  sessions: [],
  messages: [],       // 当前会话 [{role, content, message_id?}]
  streaming: false,
  controller: null,
  filter: "",
  memoryQuery: "",
};

/* ---------------- 工具 ---------------- */
function setStatus(text, isErr) {
  const el = $("statusHint");
  el.textContent = text || "";
  el.classList.toggle("err", !!isErr);
}

function applyTheme() {
  document.documentElement.dataset.theme = pref.theme;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = pref.theme === "dark" ? "#16181d" : "#4f46e5";
}

function truncate(list) {
  // 只把最近 N 条历史带给模型，避免长会话超出上下文窗口
  const n = Math.max(2, pref.contextWindow);
  return list.slice(-n).map((m) => ({ role: m.role, content: m.content }));
}

function outbound(withSystem) {
  const msgs = truncate(state.messages.filter((m) => m.content));
  if (withSystem) {
    const p = pref.persona(pref.sessionId);
    if (p) msgs.unshift({ role: "system", content: p });
  }
  return msgs;
}

function download(name, text, mime) {
  const blob = new Blob([text], { type: mime + ";charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function titleOf(text) {
  const t = (text || "").trim().replace(/\s+/g, " ");
  return t ? t.slice(0, 24) : "新对话";
}

/* ---------------- 会话侧栏 ---------------- */
async function loadSessions() {
  const data = await API.listSessions();
  state.sessions = data.sessions || [];
  renderSessions();
}

function groupLabel(iso) {
  if (!iso) return "更早";
  const d = new Date(iso);
  if (isNaN(d)) return "更早";
  const today = new Date();
  const days = Math.floor((new Date(today.getFullYear(), today.getMonth(), today.getDate())
    - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 86400000);
  if (days <= 0) return "今天";
  if (days === 1) return "昨天";
  if (days <= 7) return "近 7 天";
  if (days <= 30) return "近 30 天";
  return "更早";
}

function renderSessions() {
  const box = $("sessionGroups");
  box.innerHTML = "";
  const kw = state.filter.trim().toLowerCase();
  const list = state.sessions.filter((s) => !kw
    || (s.title || "").toLowerCase().includes(kw)
    || (s.session_id || "").toLowerCase().includes(kw));

  if (!list.length) {
    const p = document.createElement("p");
    p.className = "sb-group";
    p.textContent = kw ? "没有匹配的会话" : "还没有会话";
    box.appendChild(p);
    return;
  }

  let group = null;
  list.forEach((s) => {
    const label = groupLabel(s.created_at);
    if (label !== group) {
      group = label;
      const h = document.createElement("div");
      h.className = "sb-group";
      h.textContent = label;
      box.appendChild(h);
    }
    box.appendChild(sessionItem(s));
  });
}

function sessionItem(s) {
  const el = document.createElement("div");
  el.className = "sb-item" + (s.session_id === pref.sessionId ? " active" : "");

  const btn = document.createElement("button");
  btn.className = "title";
  btn.textContent = s.title || "新对话";
  btn.onclick = () => { switchSession(s.session_id); closeSidebar(); };

  const ops = document.createElement("div");
  ops.className = "ops";
  const del = document.createElement("button");
  del.className = "icon-btn";
  del.textContent = "×";
  del.setAttribute("aria-label", "删除会话");
  del.onclick = async (ev) => {
    ev.stopPropagation();
    if (!confirm("删除这个会话？")) return;
    await API.deleteSession(s.session_id);
    if (pref.sessionId === s.session_id) { pref.sessionId = ""; state.messages = []; renderMessages(); }
    await loadSessions();
    closeSidebar();
  };
  ops.appendChild(del);

  el.append(btn, ops);
  return el;
}

async function ensureSession() {
  if (pref.sessionId && state.sessions.some((s) => s.session_id === pref.sessionId)) return;
  const created = await API.createSession(pref.model);
  pref.sessionId = created.session_id;
  state.messages = [];
  await loadSessions();
}

async function switchSession(id) {
  pref.sessionId = id;
  const full = await API.getSession(id);
  state.messages = (full.messages || []).map((m) => ({ role: m.role, content: m.content, message_id: m.message_id }));
  renderMessages();
  renderSessions();
  syncPersonaChip();
}

async function newChat() {
  pref.sessionId = "";
  state.messages = [];
  await ensureSession();
  renderMessages();
  renderSessions();
  syncPersonaChip();
  $("input").focus();
}

/* ---------------- 渲染消息 ---------------- */
function renderMessages() {
  const host = $("messages");
  host.innerHTML = "";
  const inner = document.createElement("div");
  inner.className = "messages-inner";
  host.appendChild(inner);

  if (!state.messages.length) {
    inner.appendChild(emptyState());
    renderSuggestions(true);
    return;
  }
  renderSuggestions(false);
  state.messages.forEach((m, i) => inner.appendChild(messageNode(m, i)));
  host.scrollTop = host.scrollHeight;
}

function emptyState() {
  const el = document.createElement("div");
  el.className = "empty-state";
  const img = document.createElement("img");
  img.src = "icon.png"; img.alt = "";
  const h = document.createElement("h2");
  h.textContent = "开始一段对话";
  const p = document.createElement("p");
  p.textContent = "回复支持 Markdown 与代码高亮。侧栏「设置 → 长期记忆」可查看助手记住了什么。";
  el.append(img, h, p);
  return el;
}

const SUGGESTIONS = [
  "帮我写一个 Python 脚本批量重命名文件",
  "用一句话解释什么是向量数据库",
  "我在准备网络安全考研，帮我规划复习",
  "把这段话改得更简洁：",
];

function renderSuggestions(show) {
  const box = $("suggestions");
  box.innerHTML = "";
  if (!show) return;
  SUGGESTIONS.forEach((text) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = text;
    b.onclick = () => { $("input").value = text; autosize($("input")); $("input").focus(); };
    box.appendChild(b);
  });
}

function messageNode(m, index) {
  const node = $("msgTpl").content.firstElementChild.cloneNode(true);
  node.classList.add(m.role === "user" ? "user" : "assistant");
  node.querySelector(".msg-role").textContent = m.role === "user" ? "我" : "助手";

  const body = node.querySelector(".msg-body");
  if (m.role === "user") body.textContent = m.content;
  else MD.render(body, m.content);

  const tools = node.querySelector(".msg-tools");
  tools.querySelector('[data-act="regen"]').classList.toggle("hidden", m.role === "user" || index !== state.messages.length - 1);
  tools.querySelector('[data-act="good"]').classList.toggle("hidden", m.role !== "assistant");
  tools.querySelector('[data-act="bad"]').classList.toggle("hidden", m.role !== "assistant");
  if (m.feedback === 1) tools.querySelector('[data-act="good"]').classList.add("on");
  if (m.feedback === -1) tools.querySelector('[data-act="bad"]').classList.add("on");

  tools.querySelectorAll("button").forEach((btn) => {
    btn.onclick = () => onTool(btn.dataset.act, index, btn);
  });

  node.dataset.index = String(index);
  return node;
}

async function onTool(act, index, btn) {
  const m = state.messages[index];
  if (!m) return;
  if (act === "copy") {
    try { await navigator.clipboard.writeText(m.content); btn.textContent = "已复制"; }
    catch (_) { btn.textContent = "失败"; }
    setTimeout(() => { btn.textContent = "复制"; }, 1500);
  } else if (act === "del") {
    await replaceMessages(state.messages.filter((_, i) => i !== index));
    state.messages.splice(index, 1);
    renderMessages();
  } else if (act === "regen") {
    await regenerate(index);
  } else if (act === "edit") {
    await editMessage(index, btn);
  } else if (act === "good" || act === "bad") {
    await sendFeedback(index, act === "good" ? 1 : -1, btn);
  }
}

async function editMessage(index, btn) {
  const node = $("messages").querySelector(`.msg[data-index="${index}"]`);
  if (!node) return;
  const body = node.querySelector(".msg-body");
  body.innerHTML = "";
  const box = document.createElement("textarea");
  box.className = "edit-box";
  box.rows = 3;
  box.value = state.messages[index].content;
  body.appendChild(box);
  box.focus();

  const commit = async (save) => {
    if (!save) { renderMessages(); return; }
    const text = box.value.trim();
    if (!text) { renderMessages(); return; }
    const next = state.messages.slice(0, index);
    next.push({ role: state.messages[index].role, content: text });
    await replaceMessages(next);
    state.messages = next;
    renderMessages();
    if (state.messages[index].role === "user") await streamInto();
  };

  box.onkeydown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); commit(true); }
    if (e.key === "Escape") commit(false);
  };
  btn.textContent = "保存";
  btn.onclick = () => commit(true);
}

async function regenerate(index) {
  const keep = state.messages.slice(0, index);
  await replaceMessages(keep);
  state.messages = keep;
  renderMessages();
  await streamInto();
}

/** 依据当前 state.messages 生成一条助手回复（假定末条是 user） */
async function streamInto() {
  const holder = { role: "assistant", content: "" };
  state.messages.push(holder);
  renderMessages();
  await runStream(holder);
}

async function replaceMessages(list) {
  if (!pref.sessionId) return;
  const payload = list
    .filter((m) => !m.transient)
    .map(({ role, content, message_id }) => (message_id ? { role, content, message_id } : { role, content }));
  try {
    await API.replaceMessages(pref.sessionId, payload);
  } catch (e) {
    setStatus("同步到服务端失败：" + e.message, true);
  }
}

/* ---------------- 发送与流式 ---------------- */
async function send(text) {
  text = (text || "").trim();
  if (!text || state.streaming) return;

  try { await ensureSession(); }
  catch (e) { setStatus("会话创建失败：" + e.message, true); return; }

  state.messages.push({ role: "user", content: text });
  renderMessages();
  await streamInto();
}

async function runStream(holder) {
  state.streaming = true;
  state.controller = new AbortController();
  $("sendBtn").classList.add("hidden");
  $("stopBtn").classList.remove("hidden");
  setStatus("生成中…");

  const node = $("messages").querySelector(".messages-inner").lastElementChild;
  if (node) node.classList.add("typing");
  const body = node ? node.querySelector(".msg-body") : null;

  const sent = outbound(true);
  let failed = false;
  let aborted = false;

  try {
    const done = await API.streamChat(
      { model: pref.model, messages: sent, sessionId: pref.sessionId, signal: state.controller.signal },
      (chunk) => {
        holder.content += chunk;
        if (body) { MD.render(body, holder.content); $("messages").scrollTop = $("messages").scrollHeight; }
      });
    if (done && done.message_id) holder.message_id = done.message_id;
  } catch (e) {
    if (e.name === "AbortError") {
      aborted = true;
      holder.content = (holder.content || "") + "\n\n_（已停止生成）_";
      setStatus("已停止生成");
    } else if (e.retryable === false) {
      // 服务端已给出原因：只在当前视图显示，不写回会话历史
      failed = true;
      holder.transient = true;
      holder.content = holder.content ? holder.content + "\n\n⚠️ " + e.message : "⚠️ " + e.message;
      setStatus(e.message, true);
    } else {
      try {
        const data = await API.chat({ model: pref.model, messages: sent, session_id: pref.sessionId });
        holder.content = data.reply;
        holder.message_id = data.message_id;
      } catch (e2) {
        failed = true;
        holder.transient = true;
        holder.content = "⚠️ 请求失败：" + e2.message;
        setStatus(e2.message, true);
      }
    }
  } finally {
    if (node) node.classList.remove("typing");
    state.streaming = false;
    state.controller = null;
    $("stopBtn").classList.add("hidden");
    $("sendBtn").classList.remove("hidden");
  }

  // 服务端只会在成功时写入助手消息；本地整体回写一次以保持一致
  await persistCurrent();
  renderMessages();
  await loadSessions().catch(() => {});
  if (!failed && !aborted) setStatus("");
  $("input").focus();
}

async function persistCurrent() {
  // 用本地视图覆盖服务端会话，确保停止/编辑/删除后的历史与界面一致
  await replaceMessages(state.messages);
}

function stop() {
  if (state.controller) { try { state.controller.abort(); } catch (_) {} }
}

async function sendFeedback(index, rating, btn) {
  const m = state.messages[index];
  if (!m.message_id) { setStatus("这条回复缺少 message_id，无法提交反馈", true); return; }
  try {
    await API.feedback(m.message_id, rating);
    m.feedback = m.feedback === rating ? undefined : rating;
    renderMessages();
  } catch (e) { setStatus("反馈失败：" + e.message, true); }
}

/* ---------------- 设置面板 ---------------- */
function openSettings(tab) {
  $("settings").classList.remove("hidden");
  selectTab(tab || "memory");
  if ((tab || "memory") === "memory") loadMemories();
  if (tab === "about") loadAbout();
}
function closeSettings() { $("settings").classList.add("hidden"); }

function selectTab(name) {
  $("settingsTabs").querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $("settings").querySelectorAll(".pane").forEach((p) => p.classList.toggle("active", p.dataset.pane === name));
}

async function loadMemories() {
  const ul = $("memoryList");
  ul.innerHTML = "";
  let items = [];
  try {
    const data = state.memoryQuery
      ? await API.searchMemory(pref.userId, state.memoryQuery, 30)
      : await API.listMemory(pref.userId, 50);
    items = data.memories || data.results || [];
  } catch (e) {
    const li = document.createElement("li");
    li.textContent = "记忆服务不可用：" + e.message;
    ul.appendChild(li);
    return;
  }
  if (!items.length) {
    const li = document.createElement("li");
    li.textContent = state.memoryQuery ? "没有匹配的记忆" : "还没有记忆";
    ul.appendChild(li);
    return;
  }
  items.forEach((m) => {
    const li = document.createElement("li");
    const w = document.createElement("span");
    w.className = "w";
    w.textContent = Number(m.weight ?? (m.metadata || {}).weight ?? 1).toFixed(2);
    const txt = document.createElement("span");
    txt.className = "txt";
    txt.textContent = m.content || "";
    const del = document.createElement("button");
    del.textContent = "×";
    del.setAttribute("aria-label", "删除这条记忆");
    del.onclick = async () => {
      const id = m.id || m.memory_id;
      if (!id) { setStatus("这条记忆没有 id，无法删除", true); return; }
      await API.deleteMemory(pref.userId, [id]).catch((e) => setStatus("删除失败：" + e.message, true));
      loadMemories();
    };
    li.append(w, txt, del);
    ul.appendChild(li);
  });
}

async function loadAbout() {
  const dl = $("aboutInfo");
  dl.innerHTML = "";
  const rows = [["界面", "PWA（同源托管，可添加到主屏幕）"]];
  try {
    const stats = await API.memoryStats();
    rows.push(["记忆库", `${stats.collection_name || "-"} · ${stats.total_memories ?? "?"} 条`]);
  } catch (e) { rows.push(["记忆库", "不可用：" + e.message]); }
  rows.push(["用户标识", pref.userId]);
  rows.push(["当前模型", pref.model]);
  rows.push(["温度 / 上下文", `${pref.temperature} / ${pref.contextWindow} 条`]);
  rows.forEach(([k, v]) => {
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); dd.textContent = v;
    dl.append(dt, dd);
  });
}

function syncPersonaChip() {
  const p = pref.persona(pref.sessionId);
  $("personaChip").textContent = p ? titleOf(p) : "无角色";
  $("personaInput").value = p;
}

/* ---------------- 侧栏开合 ---------------- */
function openSidebar() { $("sidebar").classList.add("open"); $("backdrop").classList.add("show"); }
function closeSidebar() { $("sidebar").classList.remove("open"); $("backdrop").classList.remove("show"); }

function autosize(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, window.innerHeight * 0.4) + "px";
}

/* ---------------- 事件绑定 ---------------- */
function bind() {
  $("newChatBtn").onclick = () => { newChat(); closeSidebar(); };
  $("openSidebar").onclick = openSidebar;
  $("closeSidebar").onclick = closeSidebar;
  $("backdrop").onclick = closeSidebar;
  $("sessionSearch").oninput = (e) => { state.filter = e.target.value; renderSessions(); };

  $("themeBtn").onclick = () => { pref.theme = pref.theme === "dark" ? "light" : "dark"; applyTheme(); };

  $("modelSel").onchange = (e) => { pref.model = e.target.value; };
  $("exportBtn").onclick = exportCurrent;
  $("personaChip").onclick = () => openSettings("persona");

  $("chatForm").onsubmit = (e) => {
    e.preventDefault();
    const el = $("input");
    const text = el.value;
    el.value = ""; autosize(el);
    send(text);
  };
  $("stopBtn").onclick = stop;

  $("input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing && window.innerWidth > 860) {
      e.preventDefault();
      $("chatForm").requestSubmit();
    }
  });
  $("input").addEventListener("input", () => autosize($("input")));

  $("settingsBtn").onclick = () => openSettings("memory");
  $("closeSettings").onclick = closeSettings;
  $("settings").onclick = (e) => { if (e.target === $("settings")) closeSettings(); };
  $("settingsTabs").onclick = (e) => {
    const tab = e.target.closest(".tab");
    if (!tab) return;
    selectTab(tab.dataset.tab);
    if (tab.dataset.tab === "memory") loadMemories();
    if (tab.dataset.tab === "about") loadAbout();
  };

  $("memoryAddForm").onsubmit = async (e) => {
    e.preventDefault();
    const el = $("memoryInput");
    if (!el.value.trim()) return;
    try { await API.addMemory(pref.userId, el.value.trim()); el.value = ""; loadMemories(); }
    catch (err) { setStatus("添加记忆失败：" + err.message, true); }
  };
  $("memorySearchForm").onsubmit = (e) => {
    e.preventDefault();
    state.memoryQuery = $("memoryQuery").value.trim();
    loadMemories();
  };

  $("tempRange").oninput = (e) => { pref.temperature = e.target.value; $("tempVal").textContent = pref.temperature; };
  $("ctxRange").oninput = (e) => { pref.contextWindow = e.target.value; $("ctxVal").textContent = pref.contextWindow; };

  $("savePersonaBtn").onclick = () => {
    pref.setPersona(pref.sessionId, $("personaInput").value.trim());
    syncPersonaChip(); closeSettings(); setStatus("角色设定已保存");
  };
  $("clearPersonaBtn").onclick = () => {
    pref.setPersona(pref.sessionId, ""); syncPersonaChip(); setStatus("角色设定已清除");
  };

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { closeSettings(); closeSidebar(); }
  });
}

function safeFilename(text) {
  const cleaned = (text || "").replace(/[\\/:*?"<>|\r\n]+/g, " ").trim();
  return (cleaned || "对话").slice(0, 40);
}

function exportCurrent() {
  if (!state.messages.length) { setStatus("当前没有可导出的对话", true); return; }
  const name = safeFilename(titleOf(state.messages.find((m) => m.role === "user")?.content));
  const md = ["# " + name, ""]
    .concat(state.messages.filter((m) => !m.transient)
      .map((m) => `**${m.role === "user" ? "我" : "助手"}**：\n\n${m.content}\n`))
    .join("\n");
  download(`${name}.md`, md, "text/markdown");
}

/* ---------------- 启动 ---------------- */
async function loadModels() {
  const sel = $("modelSel");
  try {
    const data = await API.models();
    sel.innerHTML = "";
    (data.models || []).forEach((m) => {
      const opt = document.createElement("option");
      opt.value = m.id; opt.textContent = m.name || m.id;
      sel.appendChild(opt);
    });
    if (![...sel.options].some((o) => o.value === pref.model)) pref.model = data.default || "deepseek-chat";
    sel.value = pref.model;
  } catch (e) {
    sel.innerHTML = "";
    const opt = document.createElement("option");
    opt.value = "deepseek-chat"; opt.textContent = "DeepSeek Chat";
    sel.appendChild(opt);
    setStatus("模型列表加载失败：" + e.message, true);
  }
}

async function restore() {
  if (!pref.sessionId) return;
  try {
    const full = await API.getSession(pref.sessionId);
    state.messages = (full.messages || []).map((m) => ({ role: m.role, content: m.content, message_id: m.message_id }));
  } catch (e) {
    if (e.status === 404) { pref.sessionId = ""; state.messages = []; }
    else setStatus("会话加载失败：" + e.message, true);
  }
}

async function boot() {
  applyTheme();
  bind();
  $("tempRange").value = pref.temperature; $("tempVal").textContent = pref.temperature;
  $("ctxRange").value = pref.contextWindow; $("ctxVal").textContent = pref.contextWindow;

  await loadModels();
  try {
    await loadSessions();
    await restore();
    if (!pref.sessionId) await ensureSession();
  } catch (e) {
    setStatus("后端连接失败：" + e.message + "（请确认服务已启动，手机需与电脑同网段）", true);
  }
  renderMessages();
  syncPersonaChip();

  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js", { scope: "./" }).catch(() => {});
}

document.addEventListener("DOMContentLoaded", boot);
