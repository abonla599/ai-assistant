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
  /* 身份两件套：令牌是 /v1/auth/register 签发的那一枚（或由管理员手工给出），
   * userId 是服务端回的那个，不再是本机随机造的 web_xxxx——自造标识曾经既当
   * "用户标识"显示在关于页里，又被人以为是后端认的身份。
   */
  get token() { return localStorage.getItem("accessToken") || ""; },
  set token(v) { v ? localStorage.setItem("accessToken", v) : localStorage.removeItem("accessToken"); },
  get userId() { return localStorage.getItem("userId") || ""; },
  set userId(v) { v ? localStorage.setItem("userId", v) : localStorage.removeItem("userId"); },
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
  registering: false,     // 注册请求在途：与 streaming 同一个套路，挡双击
  controller: null,
  filter: "",
  memoryQuery: "",
  editingProvider: null,
  me: null,               // /v1/auth/me 的结果；null = 还不知道自己是谁
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

/** 401 有两种，糊成一句话会把人支使去填一个已经填对的框。
 *  - 本机压根没存过令牌：首启，该拿邀请码注册（「设置 → 连接」里就摆着注册框）；
 *  - 存了却被服务端拒：管理员撤销或轮换过。这时叫他"填写口令"，他会反复重试
 *    一枚已经作废的令牌，而真正要做的是重新注册。
 * 403 不走这里：那是"身份是真的、角色不够"，改令牌没有用。
 */
function needsAuth(err) {
  if (!err || err.status !== 401) return false;
  setStatus(pref.token
    ? "登录已失效：本机令牌已被服务端拒绝（管理员撤销或轮换过），请在「设置 → 连接」重新注册"
    : "还没有登录：请在「设置 → 连接」用邀请码注册", true);
  openSettings("conn");
  return true;
}

/* ---------------- 身份与角色 ----------------
 * 角色只决定"看得见什么"，它从来不是边界：模型服务那 7 条路由在后端就是管理员
 * 专属，手搓请求照样 403。这里把入口收起来，是为了不让普通用户点进一个只会报
 * "需要管理员权限"的面板——那句话他自己解决不了，只会以为东西坏了。
 */
function isAdmin() {
  return (state.me || {}).role === "admin";
}

async function loadWho() {
  try {
    state.me = await API.me();
  } catch (e) {
    state.me = null;
    // 401/403 是"这台设备还没登录"这一种正常状态；其余（连不上、服务端没配凭据
    // 的 503）得照原样抛出去，由 boot 说成后端连接问题。
    if (e.status !== 401 && e.status !== 403) throw e;
  }
  applyRole();
  return state.me;
}

function applyRole() {
  const admin = isAdmin();
  document.querySelectorAll("[data-admin-only]")
    .forEach((el) => el.classList.toggle("hidden", !admin));
  $("whoInfo").textContent = state.me
    ? `当前身份：${state.me.username}（${admin ? "管理员" : "普通用户"}）`
    : "未登录：用邀请码注册，或直接把管理员给您的令牌填在下面";
  if (!admin && $("paneProviders").classList.contains("active")) selectTab("conn");
}

/* ---------------- 模型服务 ---------------- */
async function loadModels() {
  const data = await API.models();
  state.providers = data.models || [];
  state.presets = data.presets || {};

  const usable = state.providers.filter((p) => p.usable);
  if (!usable.length) {
    setConn(false, "没有可用的模型服务");
    // 「模型服务」是管理员面：把普通用户推进那个页签，他只会对着 403 站着。
    if (isAdmin()) {
      setStatus("尚未配置可用的模型服务，请在「设置 → 模型服务」中添加", true);
      openSettings("providers");
    } else {
      setStatus("服务端还没有可用的模型，请联系管理员配置模型服务", true);
    }
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
function markChipFailed(chip, name, message) {
  chip.classList.add("failed");
  chip.innerHTML = "";
  const ico = document.createElement("span");
  ico.className = "att-ico";
  ico.textContent = "⚠";
  const msg = document.createElement("span");
  msg.className = "nm";
  msg.textContent = `${name}：${message}`;
  const rm = document.createElement("button");
  rm.type = "button";
  rm.textContent = "×";
  rm.setAttribute("aria-label", "移除失败的附件");
  rm.onclick = () => chip.remove();
  chip.append(ico, msg, rm);
}

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
      markChipFailed(chip, file.name, e.message);
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
    + (isAdmin() ? "模型在「设置 → 模型服务」里自行接入。"
                 : "用哪个模型由管理员在「模型服务」里配好，您只管聊。");
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
    .map(({ role, content, message_id, memory_ids, model }) => {
      const out = { role, content };
      if (message_id) out.message_id = message_id;
      if (memory_ids) out.memory_ids = memory_ids;
      if (model) out.model = model;
      return out;
    });
  try {
    await API.replaceMessages(pref.sessionId, payload);
  } catch (e) {
    if (!needsAuth(e)) setStatus("同步到服务端失败：" + e.message, true);
  }
}

/* ---------------- 发送与流式 ---------------- */
async function send(text) {
  text = (text || "").trim();
  if ((!text && !state.pending.length) || state.streaming) return;

  if (!currentProvider() || !currentProvider().usable) {
    if (isAdmin()) {
      setStatus("当前没有可用模型，请在「设置 → 模型服务」中配置", true);
      openSettings("providers");
    } else {
      setStatus("当前没有可用模型，请联系管理员配置模型服务", true);
    }
    return;
  }

  try { await ensureSession(); }
  catch (e) { if (!needsAuth(e)) setStatus("会话创建失败：" + e.message, true); return; }

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
      if (!needsAuth(e)) setStatus(e.message, true);
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
        if (needsAuth(e2)) holder.content = "⚠️ " + e2.message;
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
  selectTab(tab || (isAdmin() ? "providers" : "conn"));   // 加载由 selectTab 一处负责
}
function closeSettings() { $("settings").classList.add("hidden"); }

function selectTab(name) {
  // 模型服务这一面对普通用户全是 403：入口平时已被 applyRole 收走，这里是第二道，
  // 免得别处（默认页签、快捷键、角色切换后的旧状态）把他推进一个只会报错的表单。
  if (name === "providers" && !isAdmin()) {
    setStatus("模型服务只能由管理员配置，请联系管理员", true);
    name = "conn";
  }
  $("settingsTabs").querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $("settings").querySelectorAll(".pane").forEach((p) => p.classList.toggle("active", p.dataset.pane === name));
  if (name === "providers") loadProviders();
  if (name === "memory") loadMemories();
  if (name === "persona") syncPersonaChip();
  if (name === "conn") syncConnPane();
  if (name === "about") loadAbout();
}

function syncConnPane() {
  $("tokenInput").value = pref.token;
  $("connInfo").textContent = `当前服务：${location.origin}　·　${pref.token ? "本机已存令牌" : "本机未存令牌"}`;
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
    // 20 = 后端肯给的条数上限（api.js 的 MEMORY_TOP_K_MAX，两处由测试对齐）。
    // 以前这里发 30，后端 le=20 直接 422：搜索框永远是坏的，而看起来只是"没结果"。
    const data = state.memoryQuery
      ? await API.searchMemory(state.memoryQuery, 20)
      : await API.listMemory(50);
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
        await API.deleteMemory([id]).catch((e) => setStatus("删除失败：" + e.message, true));
        loadMemories();
      };
      li.append(w, txt, del);
      ul.appendChild(li);
    });
  } catch (e) {
    ul.appendChild(emptyItem(memoryListErrorText(e)));
  }
}

/** "记忆服务不可用"是一句会让人去做错事的话：重启后端治不了没登录。 */
function memoryListErrorText(e) {
  if (e.status === 401) return "未登录或令牌已失效：请在「设置 → 连接」重新注册";
  if (e.status === 403) return "这个账号没有读取记忆的权限，请找管理员确认";
  return "记忆服务不可用：" + e.message;
}

async function loadAbout() {
  const dl = $("aboutInfo");
  dl.innerHTML = "";
  const rows = [["界面", "PWA（同源托管，可添加到主屏幕）"],
                ["当前模型", (currentProvider() || {}).name || "未选择"],
                ["登录身份", state.me
                  ? `${state.me.username} · ${isAdmin() ? "管理员" : "普通用户"}`
                  : "未登录"]];
  // 全库统计是管理员端点：普通用户那儿的 403 不是"记忆服务坏了"，
  // 所以这一枪根本不该发（他自己的条数在「长期记忆」页签里看得见）。
  if (isAdmin()) {
    try {
      const stats = await API.memoryStats();
      rows.push(["记忆库", `${stats.collection_name || "-"} · ${stats.total_memories ?? "?"} 条`]);
    } catch (e) { rows.push(["记忆库", "读取失败：" + e.message]); }
  }
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

function setAttachMenu(open) {
  $("attachMenu").classList.toggle("hidden", !open);
  $("attachBtn").classList.toggle("open", !!open);
}

function toggleAttachMenu() {
  setAttachMenu($("attachMenu").classList.contains("hidden"));
}

/* ---------------- 网页内拍照 ----------------
 * 不用 <input capture>：部分安卓浏览器（WebView 外壳）会忽略 capture 与 accept，
 * 弹出自己的"相机/文件"面板，导致点相机还要再选一次。这里直接用 getUserMedia
 * 在页面内取景，完全不经过系统选择器。
 */
const cam = { stream: null, blob: null };

async function openCamera() {
  cam.blob = null;
  $("camHint").textContent = "";
  $("camCanvas").classList.add("hidden");
  $("camVideo").classList.remove("hidden");
  $("camShoot").classList.remove("hidden");
  $("camRetake").classList.add("hidden");
  $("camUse").classList.add("hidden");
  $("cameraModal").classList.remove("hidden");

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    const reason = "该浏览器不支持网页相机，请使用文件选择器";
    $("camHint").textContent = reason;
    setStatus(reason, true);
    closeCamera();
    return;
  }
  try {
    cam.stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" } },
      audio: false,
    });
    $("camVideo").srcObject = cam.stream;
    await $("camVideo").play().catch(() => {});
  } catch (e) {
    // 权限被拒或无摄像头：不要静默失败。提示写进状态栏——浮层马上就关了，
    // 只写在浮层里用户根本来不及看。
    const reason = `无法打开相机（${e.name || e.message}），请使用文件选择器`;
    $("camHint").textContent = reason;
    setStatus(reason, true);
    setTimeout(closeCamera, 900);
  }
}

function closeCamera() {
  if (cam.stream) {
    cam.stream.getTracks().forEach((t) => t.stop());   // 必须停轨，否则摄像头指示灯常亮
    cam.stream = null;
  }
  $("cameraModal").classList.add("hidden");
}

function shootPhoto() {
  const video = $("camVideo");
  const canvas = $("camCanvas");
  if (!video.videoWidth) { $("camHint").textContent = "相机还没准备好，稍等一下再拍"; return; }
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0);
  canvas.toBlob((blob) => { cam.blob = blob; }, "image/jpeg", 0.92);
  canvas.classList.remove("hidden");
  video.classList.add("hidden");
  $("camShoot").classList.add("hidden");
  $("camRetake").classList.remove("hidden");
  $("camUse").classList.remove("hidden");
}

function retake() {
  cam.blob = null;
  $("camCanvas").classList.add("hidden");
  $("camVideo").classList.remove("hidden");
  $("camShoot").classList.remove("hidden");
  $("camRetake").classList.add("hidden");
  $("camUse").classList.add("hidden");
}

async function usePhoto() {
  if (!cam.blob) { $("camHint").textContent = "没有拍到内容"; return; }
  const file = new File([cam.blob], `拍照-${Date.now()}.jpg`, { type: "image/jpeg" });
  closeCamera();
  const chip = attachmentChip({ name: file.name, size: file.size, kind: "?" }, true);
  $("attRow").appendChild(chip);
  try {
    const rec = await API.upload(file);
    chip.replaceWith(attachmentChip(rec));
    state.pending.push(rec);
  } catch (e) {
    markChipFailed(chip, file.name, e.message);
  }
  updateSendEnabled();
}

/* ---------------- 侧栏开合 ---------------- */
function openSidebar() { $("sidebar").classList.add("open"); $("backdrop").classList.add("show"); }
function closeSidebar() { $("sidebar").classList.remove("open"); $("backdrop").classList.remove("show"); }

function autosize(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, window.innerHeight * 0.4) + "px";
}

/* ---------------- 注册 ----------------
 * 邀请码换一枚个人令牌：这一步之后，"我是谁"才由服务端说了算。
 * 成功之后当场重取身份与数据——boot 那一次是在没有凭据的状态下跑的，模型清单
 * 和会话列表全是 401；不重跑就得叫用户手动刷新一次页面才算注册成功。
 * 全程锁住按钮：手机双击会发出第二个 POST /v1/auth/register，那枚码在第一枪里
 * 就花掉了，第二枪只能拿回 403"邀请码无效"——把已经注册成功的人显示成失败，
 * 还要两次一起抢 pref.token 与 loadServerData 的渲染顺序。
 */
async function registerWithInvite() {
  if (state.registering) return;
  const hint = $("registerHint");
  const fail = (text) => { hint.textContent = text; hint.classList.add("err"); };
  hint.classList.remove("err");
  const code = $("regCode").value.trim();
  const username = $("regUsername").value.trim();
  if (!code || !username) {
    fail("邀请码和用户名都要填");
    return;
  }
  state.registering = true;
  $("registerBtn").disabled = true;
  hint.textContent = "注册中…";
  try {
    let res;
    try {
      res = await API.register(code, username);
    } catch (e) {
      // 后端已经把原因说成人话了（邀请码无效 / 用户名已被占用），照实转述
      fail("注册失败：" + e.message);
      return;
    }
    pref.token = res.token;
    pref.userId = res.user_id;
    // 令牌已经落地，这枚码从此作废：留在输入框里等于下一次双击的素材
    $("regCode").value = "";
    syncConnPane();
    hint.textContent = `已登录为 ${res.username}，正在载入…`;
    try {
      await loadWho();
      await loadServerData();
      renderMessages();
      hint.textContent = `已登录为 ${res.username}`;
    } catch (e) {
      fail(`已登录为 ${res.username}，刷新后生效`);
      if (!needsAuth(e)) setStatus("数据载入失败：" + e.message, true);
    }
  } finally {
    // 失败也得解锁：一次网络抖动不该把注册入口按死到刷新页面为止
    state.registering = false;
    $("registerBtn").disabled = false;
  }
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
      setStatus(isAdmin() ? "该模型未配置密钥，请先在「设置 → 模型服务」补全"
                          : "该模型还没配好密钥，请联系管理员处理", true);
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
  $("pickCamera").onclick = () => { setAttachMenu(false); openCamera(); };
  $("pickImage").onclick = () => { setAttachMenu(false); $("imageInput").click(); };
  $("pickFile").onclick = () => { setAttachMenu(false); $("fileInput").click(); };
  document.addEventListener("click", (e) => {
    const menu = $("attachMenu");
    if (!menu.classList.contains("hidden") && !menu.contains(e.target)) setAttachMenu(false);
  });
  $("imageInput").onchange = (e) => pickFiles(e.target);
  $("fileInput").onchange = (e) => pickFiles(e.target);

  $("camCancel").onclick = closeCamera;
  $("camShoot").onclick = shootPhoto;
  $("camRetake").onclick = retake;
  $("camUse").onclick = usePhoto;

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
    try { await API.addMemory(el.value.trim()); el.value = ""; loadMemories(); }
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
    pref.token = $("tokenInput").value.trim();
    location.reload();   // 令牌换了就是换了人（重跑 boot 会重复绑定事件）
  };
  $("registerBtn").onclick = registerWithInvite;

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!$("cameraModal").classList.contains("hidden")) { closeCamera(); return; }
    closeSettings();
    closeSidebar();
    setAttachMenu(false);
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
    else if (!needsAuth(e)) setStatus("会话加载失败：" + e.message, true);
  }
}

/** 服务端数据的四步：注册成功、令牌变更后都要原样重跑一遍，不能只活在 boot 里。 */
async function loadServerData() {
  await loadModels();
  await loadSessions();
  await restore();
  if (!pref.sessionId && currentProvider()) await ensureSession();
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
    await loadWho();          // 先知道自己是谁：角色决定下面哪些面存在、哪些按钮该收起来
    await loadServerData();
  } catch (e) {
    if (!needsAuth(e)) {
      setConn(false, "无法连接后端");
      setStatus("后端连接失败：" + e.message, true);
    }
  }
  renderMessages();
  syncPersonaChip();

  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js", { scope: "./" }).catch(() => {});
}

document.addEventListener("DOMContentLoaded", boot);
