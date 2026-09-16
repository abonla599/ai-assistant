/* AI 智能助手 PWA 主逻辑 */
"use strict";

const $ = (id) => document.getElementById(id);

/* ---------------- 本地偏好 ---------------- */
const pref = {
  get provider() { return localStorage.getItem("provider") || ""; },
  set provider(v) { localStorage.setItem("provider", v); },
  get sessionId() { return localStorage.getItem("sessionId") || ""; },
  set sessionId(v) { v ? localStorage.setItem("sessionId", v) : localStorage.removeItem("sessionId"); },
  get temperature() { return Number(localStorage.getItem("temperature") || 0.7); },
  set temperature(v) { localStorage.setItem("temperature", String(v)); },
  get contextWindow() { return Number(localStorage.getItem("contextWindow") || 10); },
  set contextWindow(v) { localStorage.setItem("contextWindow", String(v)); },
  get theme() {
    const saved = localStorage.getItem("theme");
    if (saved) return saved;
    return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
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
  messages: [],
  providers: [],          // /v1/models 派生的清单
  presets: {},
  pending: [],            // 待发送附件 [{id,name,kind,size,url}]
  streaming: false,
  controller: null,
  filter: "",
  memoryQuery: "",
  editingProvider: null,
};

/* ---------------- 小工具 ---------------- */
function setStatus(text, isErr) {
  const el = $("statusHint");
  el.textContent = text || "";
  el.classList.toggle("err", !!isErr);
}

function setConn(ok, text) {
  $("connDot").className = "conn-dot " + (ok === null ? "" : ok ? "ok" : "bad");
  $("connText").textContent = text;
}

function applyTheme() {
  document.documentElement.dataset.theme = pref.theme;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = pref.theme === "light" ? "#ffffff" : "#0e1013";
}

function fmtSize(bytes) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + " KB";
  return (bytes / 1024 / 1024).toFixed(1) + " MB";
}

function truncate(list) {
  const n = Math.max(2, pref.contextWindow);
  return list.slice(-n).map((m) => ({ role: m.role, content: m.content }));
}

function outbound() {
  const msgs = truncate(state.messages.filter((m) => m.content && !m.transient));
  const persona = pref.persona(pref.sessionId);
  if (persona) msgs.unshift({ role: "system", content: persona });
  return msgs;
}

function download(name, text, mime) {
  const url = URL.createObjectURL(new Blob([text], { type: mime + ";charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function titleOf(text) {
  const t = (text || "").trim().replace(/\s+/g, " ");
  return t ? t.slice(0, 24) : "新对话";
}

function safeFilename(text) {
  const cleaned = (text || "").replace(/[\\/:*?"<>|\r\n]+/g, " ").trim();
  return (cleaned || "对话").slice(0, 40);
}

/** 接口返回 401 时引导到设置→连接，而不是笼统报"请求失败" */
function needsToken(err) {
  if (err && err.status === 401) {
    setStatus("需要访问口令：请在「设置 → 连接」中填写", true);
    openSettings("conn");
    return true;
  }
  return false;
}

/* ---------------- 模型服务 ---------------- */
async function loadModels() {
  const data = await API.models();
  state.providers = data.models || [];
  state.presets = data.presets || {};

  const usable = state.providers.filter((p) => p.usable);
  if (!usable.length) {
    setConn(false, "没有可用的模型服务");
    setStatus("尚未配置可用的模型服务，请在「设置 → 模型服务」中添加", true);
    openSettings("providers");
  } else {
    setConn(true, `${usable.length} 个模型可用`);
  }

  if (!usable.some((p) => p.id === pref.provider)) {
    const def = usable.find((p) => p.default) || usable[0];
    if (def) pref.provider = def.id;
  }
  renderModelSelect();
}

function renderModelSelect() {
  const sel = $("modelSel");
  sel.innerHTML = "";
  if (!state.providers.length) {
    const opt = document.createElement("option");
    opt.value = ""; opt.textContent = "未配置模型";
    sel.appendChild(opt);
    return;
  }
  state.providers.forEach((p) => {
    const opt = document.createElement("option");
    opt.value = p.id;
    if (p.usable) {
      opt.textContent = p.supports_vision ? `${p.name} · 支持图片` : p.name;
    } else {
      opt.textContent = `${p.name}（${p.reason || "不可用"}）`;
    }
    opt.disabled = !p.usable;
    sel.appendChild(opt);
  });
  sel.value = pref.provider;
}

function currentProvider() {
  return state.providers.find((p) => p.id === pref.provider) || null;
}

async function loadProviders() {
  const data = await API.providers();
  const list = $("providerList");
  list.innerHTML = "";
  (data.providers || []).forEach((p) => list.appendChild(providerRow(p)));
  $("presetRow").innerHTML = "";
  Object.entries(data.presets || {}).forEach(([key, preset]) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = preset.label;
    b.onclick = () => fillFormFromPreset(preset);
    $("presetRow").appendChild(b);
  });
}

function providerRow(p) {
  const el = document.createElement("div");
  el.className = "prov";

  const pm = document.createElement("div");
  pm.className = "pm";
  const b = document.createElement("b");
  b.textContent = p.label + (p.is_default ? "（默认）" : "");
  const s = document.createElement("span");
  s.textContent = `${p.model} · ${p.base_url} · ${p.api_key_masked || "未填密钥"}`;
  pm.append(b, s);

  const tag = document.createElement("span");
  tag.className = "tag " + (p.has_key ? "ok" : "bad");
  tag.textContent = p.has_key ? (p.supports_vision ? "可用·视觉" : "可用") : "缺密钥";

  const ops = document.createElement("div");
  ops.className = "ops";
  const mk = (text, title, fn) => {
    const btn = document.createElement("button");
    btn.className = "icon-btn";
    btn.textContent = text;
    btn.title = title;
    btn.setAttribute("aria-label", title);
    btn.onclick = fn;
    return btn;
  };
  ops.append(
    mk("✎", "编辑", () => openProviderForm(p)),
    mk("★", "设为默认", async () => {
      await API.setDefaultProvider(p.id);
      await Promise.all([loadProviders(), loadModels()]);
    }),
    mk("⚡", "测试连通", async () => {
      const box = $("provTestResult");
      box.textContent = "测试中…";
      try {
        const r = await API.testProvider(p.id);
        box.textContent = (r.ok ? "✅ " : "❌ ") + r.detail;
      } catch (e) { box.textContent = "❌ " + e.message; }
    }),
    mk("×", "删除", async () => {
      if (!confirm(`删除「${p.label}」？`)) return;
      await API.deleteProvider(p.id);
      await Promise.all([loadProviders(), loadModels()]);
    })
  );

  el.append(pm, tag, ops);
  return el;
}

function openProviderForm(p) {
  state.editingProvider = p.id;
  $("provId").value = p.id;
  $("provLabel").value = p.label;
  $("provBase").value = p.base_url;
  $("provModel").value = p.model;
  $("provVision").checked = !!p.supports_vision;
  $("provKey").value = "";
  $("provKey").placeholder = p.api_key_masked ? `已设置（${p.api_key_masked}），留空则不修改` : "填入 API Key";
  $("provTestResult").textContent = "";
  $("provForm").classList.remove("hidden");
}

function fillFormFromPreset(preset) {
  if (!state.editingProvider) $("provLabel").value = preset.label;
  $("provBase").value = preset.base_url;
  $("provModel").value = preset.model;
  $("provVision").checked = !!preset.supports_vision;
}

function closeProviderForm() {
  state.editingProvider = null;
  $("provForm").classList.add("hidden");
}

function providerDraftFromForm() {
  return {
    id: $("provId").value || undefined,
    label: $("provLabel").value.trim(),
    base_url: $("provBase").value.trim(),
    api_key: $("provKey").value.trim(),
    model: $("provModel").value.trim(),
    supports_vision: $("provVision").checked,
    is_default: false,
  };
}

async function saveProvider(ev) {
  ev.preventDefault();
  const draft = providerDraftFromForm();
  const box = $("provTestResult");
  try {
    if (state.editingProvider) {
      await API.updateProvider(state.editingProvider, draft);
    } else {
      await API.addProvider(draft);
    }
    box.textContent = "✅ 已保存";
    closeProviderForm();
    await Promise.all([loadProviders(), loadModels()]);
  } catch (e) {
    box.textContent = "❌ " + e.message;
  }
}

async function testProviderDraft() {
  const box = $("provTestResult");
  box.textContent = "测试中…";
  try {
    const r = await API.testProviderDraft(providerDraftFromForm());
    box.textContent = (r.ok ? "✅ " : "❌ ") + r.detail;
  } catch (e) {
    box.textContent = "❌ " + e.message;
  }
}

/* ---------------- 附件 ---------------- */
async function pickFiles(input) {
  const files = [...input.files];
  input.value = "";
  for (const file of files) {
    const chip = attachmentChip({ name: file.name, size: file.size, kind: "?" }, true);
    $("attRow").appendChild(chip);
    try {
      const rec = await API.upload(file);
      chip.replaceWith(attachmentChip(rec));
      state.pending.push(rec);
    } catch (e) {
      chip.classList.add("failed");
      chip.innerHTML = "";
      const ico = document.createElement("span");
      ico.className = "att-ico";
      ico.textContent = "⚠";
      const msg = document.createElement("span");
      msg.className = "nm";
      msg.textContent = `${file.name}：${e.message}`;
      const rm = document.createElement("button");
      rm.type = "button";
      rm.textContent = "×";
      rm.setAttribute("aria-label", "移除失败的附件");
      rm.onclick = () => chip.remove();
      chip.append(ico, msg, rm);
      setStatus("附件上传失败：" + e.message, true);
    }
  }
  updateSendEnabled();
}

function attachmentChip(rec, busy) {
  const el = document.createElement("div");
  el.className = "att-chip" + (busy ? " busy" : "");

  if (rec.kind === "image" && rec.url) {
    const img = document.createElement("img");
    img.src = rec.url; img.alt = "";
    el.appendChild(img);
  } else {
    const ico = document.createElement("span");
    ico.className = "att-ico";
    ico.textContent = rec.kind === "image" ? "🖼" : "📄";
    el.appendChild(ico);
  }

  const nm = document.createElement("span");
  nm.className = "nm";
  nm.textContent = rec.name;
  const sz = document.createElement("span");
  sz.className = "sz";
  sz.textContent = busy ? "上传中" : fmtSize(rec.size);
  el.append(nm, sz);

  if (!busy) {
    const rm = document.createElement("button");
    rm.type = "button";
    rm.textContent = "×";
    rm.setAttribute("aria-label", "移除附件");
    rm.onclick = () => {
      state.pending = state.pending.filter((a) => a.id !== rec.id);
      el.remove();
      updateSendEnabled();
    };
    el.appendChild(rm);
  }
  return el;
}

function clearPending() {
  state.pending.forEach((a) => a.url && URL.revokeObjectURL(a.url));
  state.pending = [];
  $("attRow").innerHTML = "";
}

function updateSendEnabled() {
  const text = $("input").value.trim();
  $("sendBtn").disabled = !state.streaming && !(text || state.pending.length);
}

async function hydrateImageUrls(atts) {
  for (const a of atts || []) {
    if (a.kind === "image" && !a.url) {
      try { a.url = await API.fileBlobUrl(a.id); } catch (_) { /* 取不到就只显示文件名 */ }
    }
  }
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
    const p = document.createElement("div");
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
  const created = await API.createSession(pref.provider);
  pref.sessionId = created.session_id;
  state.messages = [];
  await loadSessions();
}

async function switchSession(id) {
  pref.sessionId = id;
  const full = await API.getSession(id);
  state.messages = (full.messages || []).map((m) => ({
    role: m.role, content: m.content, message_id: m.message_id, attachments: m.attachments,
  }));
  await hydrateImageUrls(state.messages.flatMap((m) => m.attachments || []));
  renderMessages();
  renderSessions();
  syncPersonaChip();
}

async function newChat() {
  pref.sessionId = "";
  state.messages = [];
  clearPending();
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
  p.textContent = "支持 Markdown 与代码高亮，可上传文本/代码和图片。"
    + "模型在「设置 → 模型服务」里自行接入。";
  el.append(img, h, p);
  return el;
}

const SUGGESTIONS = [
  "帮我看看这段代码有什么问题",
  "用一句话解释什么是向量数据库",
  "我在准备网络安全考研，帮我规划复习",
  "把这段内容改得更简洁",
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

  const role = node.querySelector(".msg-role");
  role.textContent = m.role === "user" ? "我" : "助手";
  if (m.role !== "user" && m.model) role.textContent += " · " + m.model;

  const body = node.querySelector(".msg-body");
  if (m.role === "user") body.textContent = m.content;
  else MD.render(body, m.content);

  const atts = node.querySelector(".msg-atts");
  (m.attachments || []).forEach((a) => {
    if (a.kind === "image" && a.url) {
      const img = document.createElement("img");
      img.src = a.url; img.alt = a.name;
      atts.appendChild(img);
    } else {
      const tag = document.createElement("span");
      tag.className = "att-tag";
      tag.textContent = "📄 " + a.name;
      atts.appendChild(tag);
    }
  });
  if (!m.attachments) atts.remove();

  const tools = node.querySelector(".msg-tools");
  tools.querySelector('[data-act="regen"]').classList.toggle("hidden",
    m.role === "user" || index !== state.messages.length - 1);
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

function scrollBottom() {
  const host = $("messages");
  host.scrollTop = host.scrollHeight;
}

/* ---------------- 消息操作 ---------------- */
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
    next.push({ ...state.messages[index], content: text });
    await replaceMessages(next);
    state.messages = next;
    renderMessages();
    if (next[index].role === "user") await streamInto();
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
    if (!needsToken(e)) setStatus("同步到服务端失败：" + e.message, true);
  }
}

/* ---------------- 发送与流式 ---------------- */
async function send(text) {
  text = (text || "").trim();
  if ((!text && !state.pending.length) || state.streaming) return;

  if (!currentProvider() || !currentProvider().usable) {
    setStatus("当前没有可用模型，请在「设置 → 模型服务」中配置", true);
    openSettings("providers");
    return;
  }

  try { await ensureSession(); }
  catch (e) { if (!needsToken(e)) setStatus("会话创建失败：" + e.message, true); return; }

  const atts = state.pending.map((a) => ({ id: a.id, name: a.name, kind: a.kind, size: a.size, url: a.url }));
  state.messages.push({ role: "user", content: text, attachments: atts });
  clearPending();
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

  const lastUser = state.messages[state.messages.length - 2] || {};
  const sent = outbound();
  const attachments = (lastUser.attachments || []).map((a) => a.id);
  const providerId = pref.provider;
  let failed = false;
  let aborted = false;

  try {
    const done = await API.streamChat(
      { model: providerId, provider: providerId, messages: sent, attachments,
        sessionId: pref.sessionId, signal: state.controller.signal },
      (chunk) => {
        holder.content += chunk;
        if (body) { MD.render(body, holder.content); scrollBottom(); }
      });
    if (done && done.message_id) holder.message_id = done.message_id;
    if (done && done.model) holder.model = done.model;
  } catch (e) {
    if (e.name === "AbortError") {
      aborted = true;
      holder.content = (holder.content || "") + "\n\n_（已停止生成）_";
      setStatus("已停止生成");
    } else if (e.retryable === false) {
      failed = true;
      holder.transient = true;
      holder.content = holder.content ? holder.content + "\n\n⚠️ " + e.message : "⚠️ " + e.message;
      if (!needsToken(e)) setStatus(e.message, true);
    } else {
      try {
        const data = await API.chat({ model: providerId, provider: providerId,
          messages: sent, attachments, session_id: pref.sessionId });
        holder.content = data.reply;
        holder.message_id = data.message_id;
        holder.model = data.model;
      } catch (e2) {
        failed = true;
        holder.transient = true;
        if (needsToken(e2)) holder.content = "⚠️ " + e2.message;
        else { holder.content = "⚠️ " + e2.message; setStatus(e2.message, true); }
      }
    }
  } finally {
    if (node) node.classList.remove("typing");
    state.streaming = false;
    state.controller = null;
    $("stopBtn").classList.add("hidden");
    $("sendBtn").classList.remove("hidden");
  }

  await persistCurrent();
  renderMessages();
  await loadSessions().catch(() => {});
  if (!failed && !aborted) setStatus("");
  updateSendEnabled();
  $("input").focus();
}

async function persistCurrent() {
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
  selectTab(tab || "providers");
  if ((tab || "providers") === "memory") loadMemories();
  if (tab === "about") loadAbout();
}
function closeSettings() { $("settings").classList.add("hidden"); }

function selectTab(name) {
  $("settingsTabs").querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $("settings").querySelectorAll(".pane").forEach((p) => p.classList.toggle("active", p.dataset.pane === name));
  if (name === "providers") loadProviders();
  if (name === "memory") loadMemories();
  if (name === "persona") syncPersonaChip();
  if (name === "conn") syncConnPane();
  if (name === "about") loadAbout();
}

function syncConnPane() {
  const saved = localStorage.getItem("accessToken") || "";
  $("tokenInput").value = saved;
  $("connInfo").textContent = `当前服务：${location.origin}　·　口令${saved ? "已设置" : "未设置"}`;
}

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
    const data = state.memoryQuery
      ? await API.searchMemory(pref.userId, state.memoryQuery, 30)
      : await API.listMemory(pref.userId, 50);
    const items = data.memories || data.results || [];
    if (!items.length) {
      ul.appendChild(emptyItem(state.memoryQuery ? "没有匹配的记忆" : "还没有记忆"));
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
  } catch (e) {
    ul.appendChild(emptyItem("记忆服务不可用：" + e.message));
  }
}

async function loadAbout() {
  const dl = $("aboutInfo");
  dl.innerHTML = "";
  const rows = [["界面", "PWA（同源托管，可添加到主屏幕）"], ["当前模型", (currentProvider() || {}).name || "未选择"]];
  try {
    const stats = await API.memoryStats();
    rows.push(["记忆库", `${stats.collection_name || "-"} · ${stats.total_memories ?? "?"} 条`]);
  } catch (e) { rows.push(["记忆库", "不可用：" + e.message]); }
  rows.push(["用户标识", pref.userId], ["温度 / 上下文", `${pref.temperature} / ${pref.contextWindow} 条`]);
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

function setAttachMenu(open) {
  $("attachMenu").classList.toggle("hidden", !open);
  $("attachBtn").classList.toggle("open", !!open);
}

function toggleAttachMenu() {
  setAttachMenu($("attachMenu").classList.contains("hidden"));
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
  $("openSidebar").onclick = openSidebar;
  $("closeSidebar").onclick = closeSidebar;
  $("backdrop").onclick = closeSidebar;
  $("newChatBtn").onclick = () => { newChat(); closeSidebar(); };
  $("navProviders").onclick = () => { openSettings("providers"); closeSidebar(); };
  $("navMemory").onclick = () => { openSettings("memory"); closeSidebar(); };
  $("themeBtn").onclick = () => { pref.theme = pref.theme === "dark" ? "light" : "dark"; applyTheme(); };
  $("sessionSearch").oninput = (e) => { state.filter = e.target.value; renderSessions(); };

  $("modelSel").onchange = (e) => {
    const picked = state.providers.find((p) => p.id === e.target.value);
    if (picked && !picked.usable) {
      setStatus("该模型未配置密钥，请先在「设置 → 模型服务」补全", true);
      e.target.value = pref.provider;
      return;
    }
    pref.provider = e.target.value;
    setStatus("");
  };
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

  const input = $("input");
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing && window.innerWidth > 860) {
      e.preventDefault();
      $("chatForm").requestSubmit();
    }
  });
  input.addEventListener("input", () => { autosize(input); updateSendEnabled(); });

  $("attachBtn").onclick = (e) => { e.stopPropagation(); toggleAttachMenu(); };
  $("pickImage").onclick = () => { setAttachMenu(false); $("imagePicker").click(); };
  $("pickFile").onclick = () => { setAttachMenu(false); $("filePicker").click(); };
  document.addEventListener("click", (e) => {
    const menu = $("attachMenu");
    if (!menu.classList.contains("hidden") && !menu.contains(e.target)) setAttachMenu(false);
  });
  $("filePicker").onchange = (e) => pickFiles(e.target);
  $("imagePicker").onchange = (e) => pickFiles(e.target);

  $("closeSettings").onclick = closeSettings;
  $("settings").onclick = (e) => { if (e.target === $("settings")) closeSettings(); };
  $("settingsTabs").onclick = (e) => {
    const tab = e.target.closest(".tab");
    if (tab) selectTab(tab.dataset.tab);
  };

  $("addProviderBtn").onclick = () => {
    state.editingProvider = null;
    $("provId").value = ""; $("provLabel").value = ""; $("provBase").value = "";
    $("provKey").value = ""; $("provModel").value = ""; $("provVision").checked = false;
    $("provKey").placeholder = "填入 API Key";
    $("provTestResult").textContent = "";
    $("provForm").classList.remove("hidden");
    $("provLabel").focus();
  };
  $("provForm").onsubmit = saveProvider;
  $("provTestBtn").onclick = testProviderDraft;
  $("provCancelBtn").onclick = closeProviderForm;

  $("tempRange").oninput = (e) => {
    pref.temperature = e.target.value;
    $("tempVal").textContent = pref.temperature;
  };
  $("ctxRange").oninput = (e) => {
    pref.contextWindow = e.target.value;
    $("ctxVal").textContent = pref.contextWindow;
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

  $("savePersonaBtn").onclick = () => {
    pref.setPersona(pref.sessionId, $("personaInput").value.trim());
    syncPersonaChip(); closeSettings(); setStatus("角色设定已保存");
  };
  $("clearPersonaBtn").onclick = () => {
    pref.setPersona(pref.sessionId, ""); syncPersonaChip(); setStatus("角色设定已清除");
  };

  $("saveTokenBtn").onclick = () => {
    const value = $("tokenInput").value.trim();
    if (value) localStorage.setItem("accessToken", value);
    else localStorage.removeItem("accessToken");
    location.reload();   // 让所有请求带上新口令（重跑 boot 会重复绑定事件）
  };

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeSettings();
      closeSidebar();
      setAttachMenu(false);
    }
  });
}

function exportCurrent() {
  const real = state.messages.filter((m) => !m.transient);
  if (!real.length) { setStatus("当前没有可导出的对话", true); return; }
  const name = safeFilename(titleOf((real.find((m) => m.role === "user") || {}).content));
  const md = ["# " + name, ""]
    .concat(real.map((m) => `**${m.role === "user" ? "我" : "助手"}**：\n\n${m.content}\n`))
    .join("\n");
  download(`${name}.md`, md, "text/markdown");
}

/** 移动端软键盘会盖住输入框：把 body 高度收到可视视口，flex 布局即整体让位。 */
function setupKeyboardAware() {
  const vv = window.visualViewport;
  if (!vv || window.innerWidth > 860) return;

  const apply = () => {
    document.body.style.height = `${Math.round(vv.height)}px`;
    if (document.activeElement === $("input")) scrollBottom();
  };
  vv.addEventListener("resize", apply);
  vv.addEventListener("scroll", apply);
  $("input").addEventListener("focus", () => setTimeout(apply, 120));
  $("input").addEventListener("blur", () => {
    setTimeout(() => { document.body.style.height = ""; }, 150);
  });
}

/* ---------------- 启动 ---------------- */
async function restore() {
  if (!pref.sessionId) return;
  try {
    const full = await API.getSession(pref.sessionId);
    state.messages = (full.messages || []).map((m) => ({
      role: m.role, content: m.content, message_id: m.message_id, attachments: m.attachments,
    }));
    await hydrateImageUrls(state.messages.flatMap((m) => m.attachments || []));
  } catch (e) {
    if (e.status === 404) { pref.sessionId = ""; state.messages = []; }
    else if (!needsToken(e)) setStatus("会话加载失败：" + e.message, true);
  }
}

async function boot() {
  applyTheme();
  bind();
  setupKeyboardAware();
  updateSendEnabled();
  $("tempRange").value = pref.temperature;
  $("tempVal").textContent = pref.temperature;
  $("ctxRange").value = pref.contextWindow;
  $("ctxVal").textContent = pref.contextWindow;

  try {
    await loadModels();
    await loadSessions();
    await restore();
    if (!pref.sessionId && currentProvider()) await ensureSession();
  } catch (e) {
    if (!needsToken(e)) {
      setConn(false, "无法连接后端");
      setStatus("后端连接失败：" + e.message, true);
    }
  }
  renderMessages();
  syncPersonaChip();

  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js", { scope: "./" }).catch(() => {});
}

document.addEventListener("DOMContentLoaded", boot);
