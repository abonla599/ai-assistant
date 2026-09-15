/* 后端接口封装。一律使用相对路径：页面与 API 同源，
 * 手机通过局域网 IP 访问时无需任何地址配置。
 */
"use strict";

const API = (() => {
  async function request(path, { method = "GET", body, signal } = {}) {
    const res = await fetch(path, {
      method,
      signal,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      let detail = res.statusText || ("HTTP " + res.status);
      try { detail = (await res.json()).detail || detail; } catch (_) {}
      const err = new Error(detail);
      err.status = res.status;
      throw err;
    }
    return res.status === 204 ? null : res.json();
  }

  /* 流式对话。POST 无法用 EventSource，故手工读流并按空行分帧。
   * 服务端 error 事件带 retryable=false：原因已给出，上层不应再重试。
   */
  async function streamChat({ model, messages, sessionId, signal }, onChunk) {
    const res = await fetch("/v1/chat/stream", {
      method: "POST",
      signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model, messages, session_id: sessionId }),
    });
    if (!res.ok || !res.body) {
      const err = new Error("流式接口不可用 (" + res.status + ")");
      err.retryable = true;
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

  return {
    models: () => request("/v1/models"),
    createSession: (model) => request("/v1/sessions?model=" + encodeURIComponent(model), { method: "POST" }),
    listSessions: () => request("/v1/sessions"),
    getSession: (id) => request("/v1/sessions/" + encodeURIComponent(id)),
    deleteSession: (id) => request("/v1/sessions/" + encodeURIComponent(id), { method: "DELETE" }),
    replaceMessages: (id, messages) =>
      request("/v1/sessions/" + encodeURIComponent(id) + "/messages",
        { method: "PUT", body: { messages } }),
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
