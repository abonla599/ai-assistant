/* 后端接口封装。一律使用相对路径：页面与 API 同源，
 * 手机通过任何地址（局域网 IP、隧道域名）访问都无需改前端配置。
 */
"use strict";

const API = (() => {
  /** 令牌只存本机，不写入代码或仓库。
   * 它的语义在邀请码注册之后变了：不再是"共用一把访问口令"，而是服务端签发给
   * 这个人的个人令牌（管理员可单独撤销）。手工填口令那条路仍然通——那是
   * 本机直跑服务的管理员入口。
   */
  function authHeaders() {
    const token = localStorage.getItem("accessToken");
    return token ? { Authorization: "Bearer " + token } : {};
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

  async function request(path, { method = "GET", body, signal } = {}) {
    const res = await fetch(path, {
      method,
      signal,
      headers: {
        ...(body ? { "Content-Type": "application/json" } : {}),
        ...authHeaders(),
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
      headers: { "Content-Type": "application/json", ...authHeaders() },
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

  /* 图片预览：浏览器不会为 <img> 带上 Authorization 头，
   * 因此取回 blob 再造本地 URL，避免开了访问口令后缩略图全 401。
   */
  async function fileBlobUrl(uploadId) {
    const res = await fetch(`/v1/uploads/${encodeURIComponent(uploadId)}/file`,
      { headers: authHeaders() });
    if (!res.ok) throw await parseError(res);
    return URL.createObjectURL(await res.blob());
  }

  async function upload(file) {
    const fd = new FormData();
    fd.append("file", file, file.name);
    const res = await fetch("/v1/uploads", { method: "POST", body: fd, headers: authHeaders() });
    if (!res.ok) throw await parseError(res);   // 不设 Content-Type，交给浏览器带 boundary
    return res.json();
  }

  return {
    /* 身份：注册与登录都只回显一次令牌，之后一切请求都靠它。
     * me() 是前端唯一的"我到底是谁"来源——角色不能靠猜，猜错就把 403 按钮留在页面上。
     * 密码只出现在这两个请求的 body 里，绝不进任何其它请求头：运行时凭据是令牌。
     */
    register: (username, password, security_answers) =>
      request("/v1/auth/register", { method: "POST",
        body: { username, password, security_answers } }),
    login: (username, password) =>
      request("/v1/auth/login", { method: "POST", body: { username, password } }),
    recovery: (username) => request("/v1/auth/recovery", { method: "POST", body: { username } }),
    reset: (username, answers, new_password, new_answers) =>
      request("/v1/auth/reset", { method: "POST",
        body: { username, answers, new_password, new_answers } }),
    me: () => request("/v1/auth/me"),

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
