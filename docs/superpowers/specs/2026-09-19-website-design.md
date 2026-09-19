# 官网：在 www.fenever.xyz 上讲清楚这个助手能干什么

**日期：**2026-09-19
**范围：**一个新的单页落地站,由现有后端在同一台机器上出,绑自己的子域名。
**不做：**多页、博客、英文、构建工具、埋点。

---

## 0. 这个站存在的理由

现在的入口体验是残缺的：`https://ai.fenever.xyz/` 只返回一段 JSON
(`app/main.py:268`,内容形如 `{"status":"running","service":"AI 智能助手","version":"1.0.0"}`)。
朋友打开根地址看到的是机器话,而 `version: "1.0.0"` 和线上真实的 v0.13 已经不一致——
这是一句会说谎的话。

官网要达成两件事,按优先级：

1. 让一个没装过的人**三分钟内在自己手机上用起来**(打开 `/app/` 注册,或装壳)
2. 让他知道**哪些是真的能立即用的、哪些需要装壳、哪些当前没开**

受众是朋友和潜在用户,不是答辩评委,所以不出现团队分工、课程信息、技术架构图。

---

## 1. 域名与托管：走 B 路

### 1.1 Qoder Sites 为什么被排除

它的地址是「你选前缀 + 服务端追加后缀」,`check_subdomain` 的 schema 原话：
*"Check availability of a new raw address prefix… **The server appends a unique suffix.
Do not submit a hostname**"*。47 个工具里没有自定义域名的绑定或验证入口。
`www.fenever.xyz` 这条路 Sites 给不了,而纯净地址是这次的首要目标,所以不用 Sites 托管。

### 1.2 B 路的三步

现状 `~/.cloudflared/config.yml` 的 ingress 只有两条：`ai.fenever.xyz` → `http://127.0.0.1:8000`,
加一条**必填兜底** `http_status:404`。

1. `cloudflared tunnel route dns ai-assistant www.fenever.xyz`(隧道别名取自
   `tools/start-ai-stack.bat:16` 的 `tunnel run ai-assistant`)
2. `config.yml` 里在**兜底 404 之前**插入 `- hostname: www.fenever.xyz` → 同一个 `http://127.0.0.1:8000`
3. 重启 cloudflared(watchdog 只按命令行里的 `tunnel run <name>` 认进程,重启后需确认它仍认得)

**顺序就是这里唯一的坑**:兜底规则永远最后匹配,新 hostname 放在它后面会被 404 吃掉——
这个坑之前踩过一次(快速隧道那次)。

### 1.3 站点文件放哪里：`backend/app/web/site/`

不放仓库根的 `site/`。放 `backend/app/web/site/` 让三条打包链自然带上：

- `Dockerfile`：`COPY backend/ ./backend/` 已经覆盖
- `run_backend.spec`：datas 显式列目录,补一条即可
- 冻结版解包路径：和 `_static_dir()`(`web_router.py:13`)同一套 `app/web/...` 逻辑

页面本体用一根 `GET /` 的 `FileResponse` 出,静态资源沿用现成的 `RevalidatingStaticFiles`
(`web_router.py:36`,它已经带 `Cache-Control: no-cache` 和 CSP)挂在 `/site` 前缀下。

**2026-09-19 执行期改判(ledger R7)：原本这里写的是"挂 `app.mount("/")`",两处不成立**——
① `Mount("/").path` 实测是空串 `''`,契约测试的豁免名单按 `.path` 算,盖不住它;
② 根 Mount 部分匹配一切路径,会让 Starlette 的 `redirect_slashes` 不再运行,`/health/`、
`/docs/` 变 404,而 `test_auth_endpoints.py:296` 钉的正是一条尾斜杠语义。
换成 `/` 路由 + `/site` 前缀的代价,是要在 `test_route_auth_contract.py` 的 `SYSTEM_PATHS`
里显式登记 `/site` 并写理由 —— 那本来就是那把锁的设计意图,不是障碍。

Cloudflare 侧已设「尊重现有标题」,源站表态 no-cache,改版就不会在朋友那边变成"改了没生效"。

---

## 2. 六处必须同时改,漏一处就是线上事故

| # | 位置 | 不改会怎样 |
|---|------|-----------|
| 1 | `test_api.py:15` / `test_integration.py:125` / `test_web_pwa.py:101` 三处 `test_root` | 三处都在断 `/` 返回 JSON。第一轮只数到第一处(控制器 grep 加了 `head -10` 把命中截掉了),结果计划本身缺一处改动 |
| 2 | `run_backend.spec:20` 的 `datas` | 冻结版 EXE 里没这个目录,线上 404。同一形状已经崩过一次("上次 static 缺失导致 EXE 启动即崩") |
| 3 | `~/.cloudflared/config.yml` ingress 顺序 | 新主机名被兜底 404 吞掉,表现为"我这边好好的,外面打不开" |
| 4 | 源站头:`/` 与 `/site/*` 都要 no-cache | 页面走 `site_headers()`,资源走那个类,两处一致由测试各钉一半。漏了就要靠手动 Purge,且朋友会看旧页 |
| 5 | `test_route_auth_contract.py:76` 的 `SYSTEM_PATHS` 加 `/site` | 不加则 `test_nothing_routable_lives_outside_both_locks` 红——那是设计出来的闸 |
| 6 | 旧的 `@app.get("/")`(`main.py:268-275`)整段删除 | 留着它,那句 `version: "1.0.0"` 的谎还在,新页面也永远不会被读到 |

`/app`、`/admin`、`/v1/*`、`/health`、`/docs` 的语义全部不许被这次改动碰到;
`test_trailing_slash_semantics_are_untouched` 与 `test_unknown_paths_still_get_the_framework_json_404`
两条就是钉这个的,要在验收里真跑一遍,不能只靠推理。

**不动壳**：`MainActivity.java:39` 钉的是 `https://ai.fenever.xyz/app/`,官网走的是另一个
主机名,壳不需要重装(与「改界面不用重装」那条界分一致)。

---

## 3. 页面结构：六段,自上而下

| 区块 | 内容 | 事实来源 |
|------|------|---------|
| 首屏 | 一句话：一个你自己注册就能用的 AI 助手,记得住你、能算数、读得懂你发的图片和 PDF。两颗按钮：「打开就用」→ `https://ai.fenever.xyz/app/`；「装安卓壳(约 96 KB)」→ `https://github.com/abonla599/ai-assistant/releases/latest` | 手册 + v0.13 的 asset 实测 97835 字节（Release 正文那句「约 60 KB」是 v0.10 的旧数,不抄） |
| 它现在真能做的 | 多模型切换 / 计算器 / 图片和 PDF 解读 / 长期记忆 / 多人各用各的 | 只列 §4 里过了筛的 |
| 装壳多出来的三件 | 提醒(存本机不上传,重启仍生效)/ 相册·PDF 点「分享」→ 选「AI 助手」直接进附件栏 / 桌面组件直达拍照提问 | Release v0.13 正文 |
| 三步上手 | 打开链接 → 注册(自己填用户名密码,不要码)→ 开始聊 | Release v0.11+ 改口 |
| 说清楚不做什么 | 见 §4,原样写在页面上 | — |
| 常见问题 + 页脚 | 换手机记录会丢吗 / 要配 API 密钥吗 / 谁能看到我的数据 / 怎么删干净;页脚放 GitHub 与当前版本 | 用户手册 §176 起 |

命名口径：站点称「AI 智能助手」,但讲到分享面板和 App 图标时按手机上实际显示的字写
「AI 助手」——否则人在系统分享列表里找不到那个条目。

---

## 4. 文案红线(诚实账)。每条都有证据,写页面时逐条对

| 不写什么 | 证据 | 页面怎么处理 |
|---------|------|------------|
| 「沙箱执行代码」当现成能力 | `server.log`:`⚠️ Docker 不可用,代码执行沙箱暂时停用`;本机 `docker: command not found` | 归进「当前未开启」一句带过 |
| 「AI 能联网查资料」 | `web_search` 走免密钥 DuckDuckGo(`builtin_tools.py:118-139`),而这台机器 `curl` 打 ddg 三个端点全 `000` | 归进「当前未开启」,不放进主打卡片 |
| 「向作者要一次性邀请码」 | v0.11 起的 Release 说明:自己填用户名密码,"不需要找任何人要码" | 明写「不需要邀请码」 |
| Windows 桌面版 / Flutter / `Setup.exe` / `C:\Program Files\AI_Assistant` | `README.md`、`产品说明书.md`、`用户手册.md` 开头那节均已过时 | 不出现;那三份文档要不要一起改口,是另一件事,不在本 spec 范围 |
| `/admin` 入口 | 公开地址即攻击面 | 全站不出现这个路径 |
| 硬编码 `ai-assistant-0.13.apk` 文件名 | 下次发版即腐烂 | 一律链 `https://github.com/abonla599/ai-assistant/releases/latest` |

主打卡片因此只剩"计算器 + 读图读 PDF + 记忆 + 多模型"。这比"六大能力"的原始卖点窄,
但窄的那部分能当场验给来人看。

---

## 5. 截图：方案二(已授权注册演示号)

- 演示账号用户名取 `guanwang-demo`(若 `/v1/auth` 的注册校验不收连字符,退成 `guanwangdemo`);
  以注册接口的实际返回为准,不靠猜
- **口令只写在对话里,不进页面、不进文档、不进 git**
- 实拍四张：注册页 / 一次真实对话(含一次附件解读或一次计算器调用)/ 记忆页 / 设置页
- 页面里用 CSS 手机外框承载实拍图,不加任何"示意"以外的修饰,不做无中生有的美化图
- 顺手验一遍 §3 主打卡片上每一条——**没在实拍里跑通的,从页面上拿掉**
- 副作用登记:这个号会在你真实的 `users.json` 和记忆库里留下数据。**留作演示号还是删掉由你拍**;
  要删得停服务(`delete_user` 不级联清记忆)。验收时我会把账号名交给你

---

## 6. 明确不做

- 不做多页 / 博客 / 中英切换 / 暗色模式开关
- 不引入构建工具、框架、CDN 依赖(字体与图标一律系统栈或内联 SVG,零外部请求)
- 不改 `/app` 前端、不碰鉴权、不动壳
- 本次不修 `web_search` 与沙箱可达性——那两件事单独立项,不塞进官网
- 不做访问统计

---

## 7. 验收清单

本地(`uvicorn`,不依赖隧道)：
1. `http://127.0.0.1:8000/` 出官网,`content-type: text/html`
2. 回归:`/app/` `/admin/` `/health` `/docs` `/openapi.json` 全部仍为原状态,`/health` 仍 <1s
3. `pytest backend/tests/test_api.py::test_root` 按新断言通过,整套测试无新增红
4. 360×780 手机视口实拍一遍:无横向滚动、按钮热区≥44px、外链逐个 200
5. 控制台零报错;页面无任何指向 `/admin` 的链接

线上(重建 + 重启后)：
6. `dist` 产物里确实有 `app/web/site/`(**看产物,不看退出码**)
7. `www.fenever.xyz` 与 `ai.fenever.xyz/app/` 同时可用;`config.yml` 兜底 404 没吃掉新主机名
8. 手机真机:官网 → 点「打开就用」→ 能到登录/注册页
9. `Cache-Control` 头在 `/` 上确实是 `no-cache`(用 `curl -I` 看,别猜)
