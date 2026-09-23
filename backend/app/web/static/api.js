/* 后端接口封装。一律使用相对路径：页面与 API 同源，
 * 手机通过任何地址（局域网 IP、隧道域名）访问都无需改前端配置。
 */
"use strict";

const API = (() => {
  /* 方案 C：页面不再持有任何凭据明文。
   * 会话身份来自服务端签发的 httpOnly Cookie（authz.SESSION_COOKIE），浏览器对
   * 同源请求自动附带——下面每个 fetch 都显式写 credentials:"same-origin"，是把
   * 意图钉死，不让哪个引擎的默认值替我们做主。authHeaders() 保留成空对象是历史
   * 形状的墓碑：谁再往这里塞 Authorization，就是在重新把令牌喂回 JS 可读的内存。
   *
   * 仅剩两处显式带凭据的请求头，都是"凭据在调用处参数里"的一次性语义：
   * adopt(token)——把刚拿到手的明文收编成 Cookie；logout(token)——替清单里的
   * 另一个人撤销他那一枚。除此之外任何请求都不该有 Authorization。
   *
   * CSRF：不安全方法（POST/PUT/PATCH/DELETE）一律带 X-CSRF:1。后端只在
   * "没有请求头凭据而凭据出自 Cookie"时才检查它（见 authz.py），带上了对
   * 头认证客户端无害。
   */
  const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
  function authHeaders() { return {}; }
  function csrfHeaders(method) {
    return UNSAFE_METHODS.has(String(method || "").toUpperCase()) ? { "X-CSRF": "1" } : {};
  }

  /* 记忆检索的条数上限，与后端 SearchMemoryRequest.top_k 的 le=20 同源
   * （test_web_pwa 会把这两个数字对一次）。越过上限不是"少给几条"，而是整个
   * 请求 422：搜索框看上去就是坏的。所以在这里夹回来，宁可少给也要有结果。
   */
  const MEMORY_TOP_K_MAX = 20;
  function clampTopK(want) {
    const n = Number(want) || 10;
    return Math.min(MEMORY_TOP_K_MAX, Math.max(1, n));
  }

  async function parseError(res) {
    let detail = res.statusText || ("HTTP " + res.status);
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    const err = new Error(detail);
    err.status = res.status;
    return err;
  }

  /* 撤销**指定那一枚**令牌（不是当前这枚）。从本机清单里移除一个人时可以不
     带参数——只靠 Cookie 退当前会话。先把他切成当前身份再退出，等于为了删除
     而把他的会话加载到共用设备的屏幕上，所以历史上的显式 token 形参保留。 */
  async function logout(token) {
    const res = await fetch("/v1/auth/logout", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        ...(token ? { Authorization: "Bearer " + token } : {}),
        ...csrfHeaders("POST"),
      },
    });
    if (!res.ok) throw await parseError(res);
    return res.json();
  }

  /* 把一枚刚拿到手的令牌明文收编成 httpOnly 会话 Cookie（全页面唯一发
     Authorization 头的常规路径）。成功后明文就该被丢掉——app.js 里它只作为
     局部变量活过这一次调用。 */
  async function adopt(token) {
    const res = await fetch("/v1/auth/adopt", {
      method: "POST",
      credentials: "same-origin",
      headers: { Authorization: "Bearer " + token, ...csrfHeaders("POST") },
    });
    if (!res.ok) throw await parseError(res);
    return res.json();
  }

  async function request(path, { method = "GET", body, signal } = {}) {
    const res = await fetch(path, {
      method,
      signal,
      credentials: "same-origin",
      headers: {
        ...(body ? { "Content-Type": "application/json" } : {}),
        ...authHeaders(),
        ...csrfHeaders(method),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) throw await parseError(res);
    return res.status === 204 ? null : res.json();
  }

  /* 流式对话。POST 无法用 EventSource，故手工读流并按空行分帧。
   * 服务端 error 事件带 retryable=false：原因已给出，上层不应再重试。
   */
  async function streamChat({ model, provider, messages, attachments, sessionId, signal }, onChunk) {
    const res = await fetch("/v1/chat/stream", {
      method: "POST",
      signal,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...authHeaders(), ...csrfHeaders("POST") },
      body: JSON.stringify({
        model, provider, messages,
        attachments: attachments || [],
        session_id: sessionId,
      }),
    });
    if (!res.ok || !res.body) {
      const err = await parseError(res);
      err.retryable = true;      // 通道层面失败，可退回非流式
      throw err;
    }

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
          const err = new Error(evt.message || "模型返回错误");
          err.retryable = false;
          throw err;
        }
      }
    }
    if (!finished) {
      const err = new Error("流式响应未正常结束");
      err.retryable = true;
      throw err;
    }
    return finished;
  }

  /* 图片预览：改用 Cookie 会话后 <img> 那条老问题换了答案——fetch 带
   * credentials 就能认身份，blob 转本地 URL 的做法保留（它同时挡掉
   * "URL 里露凭据"的另一类泄露）。
   */
  async function fileBlobUrl(uploadId) {
    const res = await fetch(`/v1/uploads/${encodeURIComponent(uploadId)}/file`,
      { headers: authHeaders(), credentials: "same-origin" });
    if (!res.ok) throw await parseError(res);
    return URL.createObjectURL(await res.blob());
  }

  async function upload(file) {
    const fd = new FormData();
    fd.append("file", file, file.name);
    const res = await fetch("/v1/uploads", {
      method: "POST", body: fd, credentials: "same-origin",
      headers: { ...authHeaders(), ...csrfHeaders("POST") },
    });
    if (!res.ok) throw await parseError(res);   // 不设 Content-Type，交给浏览器带 boundary
    return res.json();
  }

  return {
    /* 身份：注册与登录的响应体里仍有一次性的 token（对外契约没动），但它在页面
     * 里的正确用法是当场交给 adopt() 换成 httpOnly Cookie，然后让明文自生自灭。
     * me() 是前端唯一的"我到底是谁"来源——角色不能靠猜，猜错就把 403 按钮留在页面上。
     * 密码只出现在注册/登录/改密这几个请求的 body 里，绝不进任何其它请求头。
     *
     * 这里不再有 recovery 封装：找回的三道题是全站固定常量（app.js 里那份
     * RECOVERY_QUESTIONS，test_web_pwa 会拿后端 auth.RECOVERY_QUESTIONS 逐字比一次），
     * 界面自己渲染，不必问服务器要。服务器上一次"报出问题"的响应，本质上是一份
     * "这个用户名存在吗"的名单，所以那条路由连同这个封装一起删了。
     */
    register: (username, password, security_answers) =>
      request("/v1/auth/register", { method: "POST",
        body: { username, password, security_answers } }),
    login: (username, password) =>
      request("/v1/auth/login", { method: "POST", body: { username, password } }),
    /* 三条答案 + 新密码一次提交：分开验答案就给外人一个"这个答案对不对"的 oracle。
     * new_answers（轮换找回答案）是可选的，界面不提供，于是也不该发一个 undefined 出去。
     * 成功只回 {"status": "password_reset"} 且不发令牌——该人名下所有令牌同时作废。
     */
    resetPassword: (username, answers, new_password) =>
      request("/v1/auth/reset", { method: "POST",
        body: { username, answers, new_password } }),
    me: () => request("/v1/auth/me"),
    logout: (token) => logout(token),
    // 定义在上面的 adopt()：注册/登录拿到明文后的第一步就是把它收编成 Cookie，
    // 漏了这一行，app.js 里那三处 API.adopt 会在手机上炸成"API.adopt is not a
    // function"（2026-09-23 v0.19 首发注册实测）。锁见 test_web_pwa。
    adopt,

    models: () => request("/v1/models"),
    upload,
    fileBlobUrl,

    /* 模型服务配置：整个这一面都是管理员端点（能改所有人的上游）。
     * 普通用户拿 403，所以 app.js 按角色把入口收起来，不发这一枪。
     */
    providers: () => request("/v1/providers"),
    addProvider: (rec) => request("/v1/providers", { method: "POST", body: rec }),
    updateProvider: (id, rec) => request("/v1/providers/" + encodeURIComponent(id), { method: "PUT", body: rec }),
    deleteProvider: (id) => request("/v1/providers/" + encodeURIComponent(id), { method: "DELETE" }),
    setDefaultProvider: (id) => request(`/v1/providers/${encodeURIComponent(id)}/default`, { method: "POST" }),
    testProvider: (id) => request(`/v1/providers/${encodeURIComponent(id)}/test`, { method: "POST" }),
    testProviderDraft: (rec) => request("/v1/providers/test", { method: "POST", body: rec }),

    createSession: (model) => request("/v1/sessions?model=" + encodeURIComponent(model), { method: "POST" }),
    listSessions: () => request("/v1/sessions"),
    getSession: (id) => request("/v1/sessions/" + encodeURIComponent(id)),
    deleteSession: (id) => request("/v1/sessions/" + encodeURIComponent(id), { method: "DELETE" }),
    /* 导出：换一张一次性下载票据。壳里的 WebView 收不到 blob 下载、也不会给下载
       请求带 Authorization，所以文件必须由服务端给一个真实的 https 链接。 */
    exportTicket: (id) => request(`/v1/sessions/${encodeURIComponent(id)}/export-ticket`, { method: "POST" }),
    replaceMessages: (id, messages) =>
      request("/v1/sessions/" + encodeURIComponent(id) + "/messages", { method: "PUT", body: { messages } }),
    chat: (payload) => request("/v1/chat", { method: "POST", body: payload }),
    streamChat,

    /* 记忆：身份只来自访问令牌，这些函数不收任何身份参数（也不该收）。 */
    addMemory: (content) =>
      request("/v1/memory/add", { method: "POST", body: { content, summarize: false } }),
    listMemory: (limit = 50) =>
      request(`/v1/memory/list?limit=${limit}`),
    searchMemory: (query, topK = 10) =>
      request("/v1/memory/search", { method: "POST", body: { query, top_k: clampTopK(topK) } }),
    deleteMemory: (memoryIds) =>
      request("/v1/memory/delete", { method: "DELETE", body: { memory_ids: memoryIds } }),
    // 全库统计是管理员端点：调用前先看角色（app.js 的 isAdmin），
    // 别把一个"你没权限"报成"服务挂了"。
    memoryStats: () => request("/v1/memory/stats"),
    feedback: (messageId, rating, comment) =>
      request("/v1/feedback", { method: "POST", body: { message_id: messageId, rating, comment } }),
  };
})();
