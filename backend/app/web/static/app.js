/* AI 智能助手 PWA 前端。
 * 所有接口都走相对路径：页面与 API 同源，所以手机通过局域网 IP 访问后端时
 * 无需任何地址配置——这是相对此前把 127.0.0.1 写死在客户端的根本修正。
 */
"use strict";

const $ = (id) => document.getElementById(id);
const store = {
  get model() { return localStorage.getItem("model") || "deepseek-chat"; },
  set model(v) { localStorage.setItem("model", v); },
  get sessionId() { return localStorage.getItem("sessionId") || ""; },
  set sessionId(v) { v ? localStorage.setItem("sessionId", v) : localStorage.removeItem("sessionId"); },
  get userId() {
    let id = localStorage.getItem("userId");
    if (!id) {
      id = "web_" + Math.random().toString(36).slice(2, 10);
      localStorage.setItem("userId", id);
    }
    return id;
  },
};

let sessions = [];
let messages = [];        // [{role, content}] 当前会话
let streaming = false;

/* ---------------- 基础请求 ---------------- */
async function req(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}
const jsonBody = (obj) => ({
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(obj),
});

function note(text) {
  $("connNote").textContent = text;
}

/* ---------------- 模型列表 ---------------- */
async function loadModels() {
  const sel = $("modelSel");
  try {
    const data = await req("/v1/models");
    sel.innerHTML = "";
    (data.models || []).forEach((m) => {
      const opt = document.createElement("option");
      opt.value = m.id;
      opt.textContent = m.name || m.id;
      sel.appendChild(opt);
    });
    if (![...sel.options].some((o) => o.value === store.model)) store.model = data.default || "deepseek-chat";
    sel.value = store.model;
  } catch (e) {
    sel.innerHTML = '<option value="deepseek-chat">DeepSeek Chat</option>';
    note("模型列表加载失败：" + e.message);
  }
}

/* ---------------- 会话 ---------------- */
async function loadSessions() {
  const data = await req("/v1/sessions");
  sessions = data.sessions || [];
  renderSessions();
}

function renderSessions() {
  const ul = $("sessionList");
  ul.innerHTML = "";
  if (!sessions.length) {
    ul.innerHTML = '<li class="empty">还没有会话</li>';
    return;
  }
  sessions.forEach((s) => {
    const li = document.createElement("li");
    if (s.session_id === store.sessionId) li.classList.add("active");

    const open = document.createElement("button");
    open.className = "open-session";
    open.textContent = s.title || "新对话";
    open.onclick = () => switchSession(s.session_id);

    const del = document.createElement("button");
    del.className = "del-btn";
    del.textContent = "×";
    del.setAttribute("aria-label", "删除会话");
    del.onclick = async (ev) => {
      ev.stopPropagation();
      await req("/v1/sessions/" + s.session_id, { method: "DELETE" });
      if (store.sessionId === s.session_id) { store.sessionId = ""; messages = []; renderChat(); }
      await loadSessions();
      closeDrawer();
    };

    li.append(open, del);
    ul.appendChild(li);
  });
}

async function switchSession(id) {
  const full = await req("/v1/sessions/" + id);
  store.sessionId = id;
  messages = (full.messages || []).map((m) => ({ role: m.role, content: m.content }));
  renderChat();
  renderSessions();
  closeDrawer();
}

async function ensureSession() {
  if (store.sessionId && sessions.some((s) => s.session_id === store.sessionId)) return;
  const created = await req("/v1/sessions?model=" + encodeURIComponent(store.model), { method: "POST" });
  store.sessionId = created.session_id;
  messages = [];
  await loadSessions();
}

/* ---------------- 渲染 ---------------- */
function renderChat() {
  const chat = $("chat");
  chat.innerHTML = "";
  if (!messages.length) {
    const hint = document.createElement("div");
    hint.className = "hint";
    hint.textContent = "发一条消息开始对话。\n左侧栏可以切换会话、管理长期记忆。";
    chat.appendChild(hint);
    return;
  }
  messages.forEach((m, i) => chat.appendChild(renderMsg(m, i)));
  chat.scrollTop = chat.scrollHeight;
}

function renderMsg(m, index) {
  const node = $("msgTpl").content.firstElementChild.cloneNode(true);
  node.classList.add(m.role === "user" ? "user" : "assistant");
  node.querySelector(".bubble").textContent = m.content;

  if (m.role === "assistant") {
    const acts = node.querySelector(".actions");
    acts.classList.remove("hidden");
    acts.querySelectorAll(".fb-btn").forEach((btn) => {
      btn.onclick = async () => {
        const mid = m.message_id;
        if (!mid) { note("这条回复缺少 message_id，无法反馈"); return; }
        try {
          await req("/v1/feedback", { method: "POST", ...jsonBody({ message_id: mid, rating: Number(btn.dataset.rating) }) });
          acts.querySelectorAll(".fb-btn").forEach((b) => b.classList.remove("done"));
          btn.classList.add("done");
        } catch (e) { note("反馈失败：" + e.message); }
      };
    });
    if (m.feedback) {
      const picked = acts.querySelector(`[data-rating="${m.feedback}"]`);
      if (picked) picked.classList.add("done");
    }
  } else {
    node.querySelector(".actions").remove();
  }
  node.dataset.index = String(index);
  return node;
}

function scrollBottom() {
  const chat = $("chat");
  chat.scrollTop = chat.scrollHeight;
}

/* ---------------- 发送 ---------------- */
async function send(text) {
  if (streaming || !text.trim()) return;
  streaming = true;
  $("sendBtn").disabled = true;

  try {
    await ensureSession();
  } catch (e) {
    note("会话创建失败：" + e.message);
    streaming = false;
    $("sendBtn").disabled = false;
    return;
  }

  messages.push({ role: "user", content: text.trim() });
  renderChat();

  const holder = { role: "assistant", content: "" };
  messages.push(holder);
  const live = $("chat").lastElementChild;
  if (live) live.classList.add("typing");
  const bubble = live ? live.querySelector(".bubble") : null;

  const payload = { model: store.model, messages: messages.slice(0, -1).map(pick), session_id: store.sessionId };

  try {
    const done = await streamChat(payload, (chunk) => {
      holder.content += chunk;
      if (bubble) { bubble.textContent = holder.content; scrollBottom(); }
    });
    if (done && done.message_id) holder.message_id = done.message_id;
  } catch (e) {
    if (e.retryable === false) {
      holder.content = e.message;   // 服务端已说明原因，不再重复请求
    } else {
      // 流式通道不可用时退回一次性请求，保证功能不中断
      try {
        const data = await req("/v1/chat", { method: "POST", ...jsonBody(payload) });
        holder.content = data.reply;
        holder.message_id = data.message_id;
      } catch (e2) {
        holder.content = "请求失败：" + e2.message;
      }
    }
  }

  if (live) live.classList.remove("typing");
  renderChat();
  await loadSessions().catch(() => {});
  streaming = false;
  $("sendBtn").disabled = false;
  $("msgInput").focus();
}

const pick = (m) => ({ role: m.role, content: m.content });

/* 解析 SSE：POST 请求无法用 EventSource，所以手工读取流并按空行分帧。 */
async function streamChat(payload, onChunk) {
  const res = await fetch("/v1/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok || !res.body) throw new Error("流式接口不可用 (" + res.status + ")");

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buf = "";
  let finished = null;

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const line = frame.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      let evt;
      try { evt = JSON.parse(line.slice(5).trim()); } catch (_) { continue; }
      if (evt.type === "content") onChunk(evt.text || "");
      else if (evt.type === "done") finished = evt;
      else if (evt.type === "error") {
        const err = new Error(evt.message || "流式出错");
        err.retryable = false;   // 服务端已给出原因，重试只会再失败一次
        throw err;
      }
    }
  }
  if (!finished) throw new Error("流式响应未正常结束");
  return finished;
}

/* ---------------- 记忆 ---------------- */
function emptyItem(text) {
  const li = document.createElement("li");
  li.className = "empty";
  li.textContent = text;
  return li;
}

async function loadMemories() {
  const ul = $("memoryList");
  ul.innerHTML = "";
  try {
    const data = await req("/v1/memory/list/" + encodeURIComponent(store.userId) + "?limit=12");
    const items = data.memories || data.results || [];
    if (!items.length) {
      ul.appendChild(emptyItem('暂无记忆（说句"记住…"试试）'));
      return;
    }
    items.forEach((m) => {
      const li = document.createElement("li");
      const w = document.createElement("span");
      w.className = "w";
      w.textContent = Number(m.weight ?? m.metadata?.weight ?? 1).toFixed(2);
      const txt = document.createElement("span");
      txt.textContent = m.content || "";
      li.append(w, txt);
      ul.appendChild(li);
    });
  } catch (e) {
    ul.appendChild(emptyItem("记忆服务不可用：" + e.message));
  }
}

async function addMemory(text) {
  await req("/v1/memory/add", {
    method: "POST",
    ...jsonBody({ user_id: store.userId, content: text, summarize: false }),
  });
  await loadMemories();
}

/* ---------------- 侧栏 ---------------- */
function openDrawer() { $("drawer").classList.add("open"); $("scrim").classList.remove("hidden"); }
function closeDrawer() { $("drawer").classList.remove("open"); $("scrim").classList.add("hidden"); }

/* ---------------- 启动 ---------------- */
function bind() {
  $("menuBtn").onclick = () => ($("drawer").classList.contains("open") ? closeDrawer() : openDrawer());
  $("scrim").onclick = closeDrawer;

  $("newChatBtn").onclick = async () => {
    store.sessionId = "";
    messages = [];
    renderChat();
    await ensureSession();
    renderSessions();
    closeDrawer();
  };

  $("modelSel").onchange = (e) => { store.model = e.target.value; };

  $("chatForm").onsubmit = (e) => {
    e.preventDefault();
    const input = $("msgInput");
    const text = input.value;
    input.value = "";
    autosize(input);
    send(text);
  };

  const input = $("msgInput");
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && window.innerWidth > 720) {
      e.preventDefault();
      $("chatForm").dispatchEvent(new Event("submit"));
    }
  });
  input.addEventListener("input", () => autosize(input));

  $("memoryForm").onsubmit = async (e) => {
    e.preventDefault();
    const box = $("memoryInput");
    if (!box.value.trim()) return;
    try { await addMemory(box.value.trim()); box.value = ""; }
    catch (err) { note("添加记忆失败：" + err.message); }
  };

  $("refreshMemoryBtn").onclick = () => loadMemories();
}

function autosize(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, window.innerHeight * 0.4) + "px";
}

async function restoreSession() {
  if (!store.sessionId) return;
  try {
    const full = await req("/v1/sessions/" + store.sessionId);
    messages = (full.messages || []).map((m) => ({ role: m.role, content: m.content }));
  } catch (_) {
    store.sessionId = "";   // 会话已被删除
  }
}

async function boot() {
  bind();
  await loadModels();
  try {
    await loadSessions();
    await restoreSession();
    note("");
  } catch (e) {
    note("后端连接失败：" + e.message);
  }
  renderChat();
  loadMemories();

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js", { scope: "./" }).catch(() => {});
  }
}

document.addEventListener("DOMContentLoaded", boot);
