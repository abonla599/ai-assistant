/* 后端接口封装。一律使用相对路径：页面与 API 同源，
 * 手机通过任何地址（局域网 IP、隧道域名）访问都无需改前端配置。
 */
"use strict";

const API = (() => {
  /** 访问口令仅存本机，不写入代码或仓库 */
  function authHeaders() {
    const token = localStorage.getItem("accessToken");
    return token ? { Authorization: "Bearer " + token } : {};
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
    models: () => request("/v1/models"),
    upload,
    fileBlobUrl,

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

    addMemory: (userId, content) =>
      request("/v1/memory/add", { method: "POST", body: { user_id: userId, content, summarize: false } }),
    listMemory: (userId, limit = 50) =>
      request(`/v1/memory/list/${encodeURIComponent(userId)}?limit=${limit}`),
    searchMemory: (userId, query, topK = 10) =>
      request("/v1/memory/search", { method: "POST", body: { user_id: userId, query, top_k: topK } }),
    deleteMemory: (userId, memoryIds) =>
      request("/v1/memory/delete", { method: "DELETE", body: { user_id: userId, memory_ids: memoryIds } }),
    memoryStats: () => request("/v1/memory/stats"),
    feedback: (messageId, rating, comment) =>
      request("/v1/feedback", { method: "POST", body: { message_id: messageId, rating, comment } }),
  };
})();
