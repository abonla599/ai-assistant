# 第三方前端库

本目录下的库为离线打包（PyInstaller EXE）与手机无外网场景而随仓库分发，版本固定。

| 文件 | 库与版本 | 许可证 | 用途 |
|---|---|---|---|
| `marked.min.js` | marked v12.0.2 | MIT | Markdown 解析 |
| `purify.min.js` | DOMPurify 3.1.6 | Apache-2.0 / MPL-2.0 双许可 | 净化模型输出的 HTML，防 XSS |
| `highlight.min.js` | highlight.js 11.9.0 | BSD-3-Clause | 代码块语法高亮 |
| `hljs-github-dark.min.css` | highlight.js 11.9.0 主题 | BSD-3-Clause | 高亮配色 |

来源：jsDelivr CDN（`cdn.jsdelivr.net/npm/...`、`cdn.jsdelivr.net/gh/highlightjs/cdn-release@...`）。

**必须保留 DOMPurify**：模型回复按 Markdown 渲染成 HTML，若不做净化，
回复内容中的标签可直接执行脚本。
