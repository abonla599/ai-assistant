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

  /* highlight.js 占前端全部 JS 的近七成，却只在出现代码块时才用得上：改成首次
   * 遇到代码块时才动态加载，别让它堵在首屏关键路径上。请求只发一次；加载完成
   * 后把页面上尚未上色的块统一补齐。
   */
  let hljsLoading = false;

  function highlightPending() {
    // 只碰还没被上色过的块：hljs v11 会在元素上打 data-highlighted 标记，
    // 重复 highlight 同一块只会刷一串 console 警告。
    document.querySelectorAll("pre > code").forEach((code) => {
      if (code.dataset.highlighted) return;
      // 流式渲染中的容器（带 data-live）里是半截代码，此刻高亮是无效功：
      // hljs 加载完成的时机可能正落在流中间，等流结束 renderMessages 的
      // 全量渲染会补上。逐级 parentNode 检查而不是 closest：少一个
      // 老 WebView 兼容性假设，document 节点没有 getAttribute 也被挡住。
      for (let p = code.parentElement; p; p = p.parentElement) {
        if (p.getAttribute && p.getAttribute("data-live") !== null) return;
      }
      try { hljs.highlightElement(code); } catch (_) { /* 语言不支持时忽略 */ }
    });
  }

  function ensureHljs() {
    if (typeof hljs !== "undefined" || hljsLoading) return;
    hljsLoading = true;
    const s = document.createElement("script");
    s.src = "vendor/highlight.min.js";
    s.onload = () => { hljsLoading = false; highlightPending(); };
    s.onerror = () => {
      // 加载失败：清掉这具 script 尸体再放行重试，否则每失败一次 head 里
      // 就多挂一个死标签，弱网下反复重试会一直堆积。
      s.remove();
      hljsLoading = false;
    };
    document.head.appendChild(s);
  }

  /** 把渲染结果挂到容器上，并处理代码块高亮与复制按钮。
   *  live 为真表示流式渲染中：跳过高亮（正则密集、每帧全量跑是 O(n²)），
   *  留给流结束后 renderMessages 的那次全量渲染补上。净化与链接处理
   *  每次都照做——净化是安全不变量，绝不跳过。 */
  function render(container, text, live) {
    container.innerHTML = toHtml(text);

    container.querySelectorAll("a[href]").forEach((a) => {
      a.setAttribute("target", "_blank");
      a.setAttribute("rel", "noopener noreferrer");
    });

    container.querySelectorAll("pre > code").forEach((code) => {
      if (typeof hljs === "undefined") {
        ensureHljs();
      } else if (!live) {
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
