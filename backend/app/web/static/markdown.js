/* Markdown 渲染：marked 解析 -> DOMPurify 净化 -> highlight.js 上色。
 *
 * 净化不是可选项：模型回复被当作 Markdown 渲染成 HTML，若不过滤，
 * 回复内容里的 <img onerror> 之类可直接在前端执行脚本。
 */
"use strict";

const MD = (() => {
  if (typeof marked === "undefined") return null;

  marked.setOptions({ gfm: true, breaks: true });

  const raw = typeof DOMPurify !== "undefined"
    ? DOMPurify.sanitize
    : (s) => String(s).replace(/[<>&]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c]));

  const ALLOWED = { ADD_ATTR: ["target", "rel", "data-code"] };

  function toHtml(text) {
    if (!marked) return escapeHtml(text);
    const html = marked.parse(text || "");
    return raw(html, ALLOWED);
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  }

  /** 把渲染结果挂到容器上，并处理代码块高亮与复制按钮 */
  function render(container, text) {
    container.innerHTML = toHtml(text);

    container.querySelectorAll("a[href]").forEach((a) => {
      a.setAttribute("target", "_blank");
      a.setAttribute("rel", "noopener noreferrer");
    });

    container.querySelectorAll("pre > code").forEach((code) => {
      if (typeof hljs !== "undefined") {
        try { hljs.highlightElement(code); } catch (_) { /* 语言不支持时忽略 */ }
      }
      const pre = code.parentElement;
      const bar = document.createElement("div");
      bar.className = "code-bar";
      const lang = (code.className.match(/language-([\w-]+)/) || [, "text"])[1];
      bar.innerHTML = `<span class="code-lang"></span>`;
      bar.querySelector(".code-lang").textContent = lang;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "code-copy";
      btn.textContent = "复制";
      btn.onclick = async () => {
        try {
          await navigator.clipboard.writeText(code.innerText);
          btn.textContent = "已复制";
        } catch (_) {
          btn.textContent = "失败";
        }
        setTimeout(() => { btn.textContent = "复制"; }, 1600);
      };
      bar.appendChild(btn);
      pre.insertBefore(bar, pre.firstChild);
    });

    return container;
  }

  return { render, escapeHtml };
})();
