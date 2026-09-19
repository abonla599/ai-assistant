# 用户自带模型：浏览器直连 + 纯对话（形状 A）

日期：2026-09-19　状态：设计已拍定，**实现排在壳三项之后**（用户 2026-09-19 选「先落地壳三项」）

## 0. 这条推翻了 2026-09-18 的裁决

上一轮拍的是"服务器代发调用供应商、但不持久化 key"。本轮用户看了 Qoder 桌面版的存储结构对比后
改主意：「既然 CORS 实测通，就别当代理」。我补验了他没验的那个关键头，直连确实成立，于是接受改判。

**随之作废**：出网 IP 白名单（loopback/RFC1918/CGNAT/169.254.169.254/::1）、`follow_redirects=False`、
聊天请求里的一次性 `credential:{base_url,api_key,model}` 字段、以及"转发完就丢"这条需要长期正确的约定。
SSRF 面与服务器日志面归零。

**直连并没有把局域网 Ollama 放回来**：https 页面 fetch `http://192.168.x.x:11434` 会被浏览器按混合内容
掐掉，且 `MainActivity.java:73` 是 `MIXED_CONTENT_NEVER_ALLOW`。只是把墙从"服务端 SSRF 白名单"
换成"更难绕的混合内容"。除非给 Ollama 配 TLS，别把它说成自由了。

## 1. 实测：谁能进"直连"下拉

OPTIONS 预检带 `Access-Control-Request-Headers: authorization,content-type`，Origin 用
`https://ai.fenever.xyz`（2026-09-19 测）：

| 厂商 | base_url | 预检结果 | 进下拉 |
| --- | --- | --- | --- |
| DeepSeek | `https://api.deepseek.com/v1` | 回显 origin + `authorization,content-type`，只放 POST | ✅ |
| Kimi | `https://api.moonshot.cn/v1` | 回显 origin + 同上 | ✅ |
| 智谱 | `https://open.bigmodel.cn/api/paas/v4` | 回显 origin + 同上 | ✅ |
| 硅基流动 | `https://api.siliconflow.cn/v1` | `*` + `*` | ✅ |
| 通义百炼 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `*` + 同上 + `max-age 1800` | ✅ |
| OpenAI | `https://api.openai.com/v1` | OPTIONS 与 POST 均 `000`（本机出口连不上） | ❓ 测不出 |
| Groq | `https://api.groq.com/openai/v1` | OPTIONS 与 POST 均 403（Cloudflare 挡） | ❓ 测不出 |

**测不出 ≠ 不支持**：这两条请求根本没完成，拿不到厂商策略。但结论是工程性的——**放进下拉就是骗人**，
因为用户填了 key 只会得到一条长得像"你的 key 不对"的失败。所以首版下拉只放上面五家 + 一项"自定义"。

## 2. 清单三层分家

抄的是 Qoder 的结构，不是它的存储位置。

- **内置目录**：现有管理员那 7 条 providers 路由管的东西。保持只读、全局、可缓存，**不加用户 key 字段**。
- **覆盖表 `user_models`**：按 owner 存，字段
  `{id, label, base_url, model, supports_vision, api_type}`。
  **这张表没有 key 这一列**——不是"接口不返回 key"，是服务端没有可泄露的东西。
  `supports_vision` 必须在这张表里：它现在是 provider 属性且在服务端被消费
  （`providers.py:56-59` → `_prepare_chat` → `uploads.py:277-299` 决定图片走多模态数组还是直接报错），
  而消息仍由服务端组装，所以服务端必须知道；由用户在添加模型时自己声明。
- **每人选中项**：跟人走，存在身份清单那一条里（`2026-09-18-account-switching-design.md` 已拍）。

合并点在 `provider_store.resolve()`：先查调用者的覆盖层，未命中再查内置目录，都无则 **404**
（沿用 404-not-403，不让它变成探测器）。

顺手修掉挂了很久的缺陷：默认 provider 前后端漂移——服务端 `providers.default()` 不看 key 可用性，
前端却是 `usable.find(p => p.default) || usable[0]`。合并后以服务端为准，前端不再自己挑。

## 3. 两步协议

**先纠一处本文件初稿说错的话**：初稿写的是"`pipeline.py:129-158` 那个服务端工具循环是边界的来源"。
核对后不成立——PWA 走的是流式，`main.py:424` 调的是 `streaming.stream_chat(...)`，
而它**没有 `tools` 参数**（`streaming.py:32-38`）；那个循环只在非流式 `/v1/chat` 的
`ChatPipeline.process` 里，前端根本不碰。所以**今天 PWA 一个工具都调不到**。

这条改判对本设计有两处影响：① "自带模型不带工具"不是新增的阉割，而是对现状的如实命名，
A 的代价比初稿估的小；② 但它依赖一个易碎的前提——一旦有人把 tool loop 接进流式
（那是另一份 P0 清单里的头号项），这条边界立刻从"描述"变成"真限制"，届时必须重开本设计。

`used_memory_ids` 的约束不受影响：召回发生在 `pipe.inject_context()`（`main.py:405`），
它和调模型是分开的两步，所以"服务端组装、客户端调模型"这个协议本来就成立。

- `POST /v1/chat/prompt {provider, session_id, messages, attachments}`
  → 鉴权 + `_require_session_owner` + 新的 `ChatPipeline.assemble()`（记忆召回、附件拼装、vision 判定）
  → 返回 `{turn_id, messages, model, api_type}`。
  `turn_id` 是一次性票据，**`used_memory_ids` 存在票据里，绝不让客户端回传**——那等于让它决定哪些记忆
  被计入、被衰减。形状直接复用 `session/export_store.py` 那套（pop-then-check、单调时钟可注入、
  懒清扫、锚定的正则白名单）。
- 浏览器用返回的 messages 直连供应商，自己解析 SSE，失败上屏带类型与根因（前端等价物，
  对齐后端 `_fail_reason` 的表达力）。
- `POST /v1/chat/commit {turn_id, reply}` → 落盘会话、写记忆、更新权重。

**新出现的注入面**：`commit` 让客户端往服务端会话里写"助手说的话"。三条反例测试必须写死：
① turn_id 与 owner 绑定，跨 owner 使用回 404；② 一次一用，重放回 404；③ `reply` 有长度上限
（否则任何人能往自己历史里塞 10MB 文本，且它会被当成模型输出喂进下一轮上下文）。

## 4. 能力边界写在脸上

选了自带模型，输入框上方一条明确提示：**"自带模型只支持纯对话，不带工具与任务"**。不藏、不用灰色
图标暗示。记忆、附件、会话持久化、导出全部照旧（组装与落盘都在服务端）。

## 5. 安全边界

- 服务端不再对供应商发起任何请求 → SSRF 面归零，`/v1/*` 的出网行为不变。
- "只放公网 https"这条限制由浏览器的混合内容规则自动执行，本设计一行代码都不写。
- `/v1/chat/prompt` 的入参里没有 key，日志天然干净——这是 A 最实在的收益。
- **残余风险如实记录**：key 存在 localStorage，仍在你自己站点上一处 XSS 的射程内。缓解是 DOMPurify
  （已用）+ `Content-Security-Policy: frame-src 'none'` 那一行。**那一行随壳那批先落地**
  （见 `2026-09-19-shell-native-capabilities-design.md` §2 裁决②），本设计依赖它。
- 比 localStorage 高一档的做法是 Keystore + 原生代发（形状 C）。**本版不做**，理由：若桥开
  `getSecret()`，"XSS 调桥"与"XSS 读 localStorage"在同一沙盒内，零收益；真有收益要求 key 永不进 JS，
  那要引两套传输与约 300 行原生 HTTP+SSE。A 的 `prompt`/`commit` 协议在 C 下**一字不改**，
  只是 fetch 从浏览器挪进壳——所以先 A 不返工。

## 6. 限流

新起一本账（第五本），键为 `IP + user`，覆盖 `/v1/chat/prompt` 与 `/v1/chat/commit`。
理由：`prompt` 会打 chroma 向量检索，是真成本；`commit` 是写端点。沿用既有脾气——
按 `CF-Connecting-IP` 记、各本账按自己的窗口 prune、**任何成功都不还回预算**、计费挂标记不挂文案。
窗口与阈值初值定为 **60 秒内 20 次**（prompt 与 commit 合计），实现时按实测的单次 chroma 查询
耗时调整；调了要改这里，别只改代码。

## 7. 界面

按用户给的那张截图形状做"添加模型"弹层：供应商下拉 / API 类型 / Base URL / API Key（带眼睛图标）/
Model ID（可多个）。**外加一个「支持图片」开关**——截图里没有这一项，但 §2 说清了服务端组装消息时
必须知道 `supports_vision`，缺它就只能靠猜，猜错的后果是"图片被静默丢掉"或"纯文本模型收到图片直接报错"。
保存前用 4 个 token 做一次真实连通测试——**这次由浏览器直连测**，
不经过服务端，所以测试请求也不碰你的服务器。
列表项显示 `label + model + 供应商`，不显示 key（服务端没有，前端也只在编辑时回填一次）。

## 8. 明确不做

用量统计与计费（自带 key 不消耗管理员额度）、团队共享模型、形状 C、OpenAI/Groq 进下拉、
per-user 的 providers CRUD（那 7 条路由保持管理员专属）、新增 `PUBLIC_PATHS`。

## 9. 实现前必须先验的一件事

在**真机浏览器**（手机上，不是桌面 headless）跑通一次 DeepSeek 直连：SSE 增量解析、
中途断网、401/429 的错误文案。桌面 CDP 环境能验协议，但混合内容与 CORS 在移动 WebView 里的
行为差异只有实机能定论。跑不通就不该动 `pipeline.py`。
