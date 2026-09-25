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
| 首屏 | 一句话：一个你自己注册就能用的 AI 助手,记得住你、能算数、读得懂你发的 PDF（图片按 R13 收窄,见 §8.4）。两颗按钮：「网页版」→ `https://ai.fenever.xyz/app/`；「装安卓壳(约 96 KB)」→ `https://github.com/abonla599/ai-assistant/releases/latest` | 手册 + v0.13 的 asset 实测 97835 字节（Release 正文那句「约 60 KB」是 v0.10 的旧数,不抄） |
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

---

## 8. 上线后改判(2026-09-20)

上线之后这一轮又改了几处,其中三处直接推翻了 §3/§6 的原话。留在这里而不是回头删改,
是因为 §4 那张诚实账的每条证据都还成立,变的是"页面该长成什么样"。

1. **默认深色 + 主题开关**——§6 原本写「不做暗色模式开关」,推翻了。理由很实在:四张实拍
   是深色 App 界面,挂在浅色页上两边都难看(截图是资产,不能为了配色重拍)。开关只在
   深/浅两档间切,选择存 `localStorage`,首屏前用一段内联脚本定色避免闪白。仍然零外部请求。
2. **加了交互**：滚动渐显、截图轮播、回到顶部、复制当前链接。全部手写,不引库、不进构建。
   判据不是"有没有 JS",而是"有没有外部请求"——`test_scripts_are_same_origin_only` 扫的
   是 `site.js` 里不得出现 `http(s)://` 字面量。
3. **渐显必须有兜底**：IntersectionObserver 没触发过一次整页空白(`revealedCount 0/11`)。
   现在 1.5 秒强制全显 + 监听 `pageshow`,浏览器动画被降级时最坏是"没有渐入",不是白屏。
4. **「读得懂图片」从首屏和主打卡片撤下**(R13)：本机视觉模型的密钥是占位符,发图只会收到
   一句拒绝。页面把它归进「当前未开启」,PDF/txt 解读留在卡片里——那条是当场跑通过的。
5. **演示号已注销**(§5 登记的副作用按用户指令收回)：`guanwang-demo` 走接口删除,残留数据
   停服务清理,前后计数 `sessions 28→25`、`uploads 10→6`、`chroma 79→71`,复查 0 残留。
   四张截图作为静态资产保留。
6. **页脚不写当前版本号**：原来那句「当前版本 v0.13（2026-09-19）」在 v0.14 发出那天就已经是
   谎,而这一轮才删掉。理由是 §4「不硬编码 apk 文件名」的同一条:凡是能从别处实时读到、
   且会自己变的事实,不抄进静态页面。现在页脚只链 Releases。
   新增 `test_the_page_never_states_a_version_number` 扫 `v\d+\.\d+`,把这句钉住。
7. **四张实拍分优先级**：第一张 `fetchpriority="high"`(它是这一页的 LCP),其余三张
   `loading="lazy"`——约 500 KB 的图,移动网络上一起下载就是拿三张还没露面的图抢首屏。
   但光加 lazy 会翻车：轮播的图横向摆在 `overflow:hidden` 的轨道里,浏览器始终不认为它们
   "快滚进视口",翻到第二张只剩一个空壳(实测)。所以 site.js 在 `load` 之后把它们提升成
   eager。`test_only_the_first_screenshot_loads_eagerly` 两头都钉住。
8. **工具清单按实测可用性裁剪**：§6「本次不修 web_search 与沙箱可达性」仍然成立——没去修
   可达性,改的是"不再把注定失败的工具递给模型"。判据是探测出来的(`availability.py`
   后台每 5 分钟重连一次搜索源、沙箱读那唯一 `SandboxManager` 实例的 `client`),
   不是写死的名单:装上 Docker、换个搜索源,工具自己回来。检查本身异常时按"可用"处理
   (误开只是多一次失败调用,误关是悄悄藏掉一个能用的功能,后者没人会去查)。
   见 `backend/tests/test_tool_availability.py`。
9. **390px 视口下量出来的三处修正**：顶栏在 ≤560px 只留品牌名 + 主题开关（四个锚点链接
   挤进去会把品牌名折成两行、最后一个链接切一半）；轮播指示点的热区从 9×9 提到 26×44
   （看得见的药丸交给 `::before`）；`html { scroll-padding-top }` 给 sticky 顶栏让位,
   否则点导航跳过去的标题正好压在栏下。
   量法记一下：headless Edge 的 `--window-size=390` 会被最小窗宽撑开再裁,看到的"横向溢出"
   是假象,要用 CDP 的 `Emulation.setDeviceMetricsOverride` 才是真 390。
10. **轮播撤了，四张实拍改成一次全铺**（2026-09-22）。他问"怎么就只剩下一张图片了"——
   不是 bug：实测四张都下载完（`loaded [1,1,1,1]`）、自动播和前后键都在工作（5.6 秒在第
   一张、10.1 秒已平移到第二张）。"一次只看得见一张"就是轮播的定义。但四张静态实拍没有
   非藏起来不可的理由，那套机器反而要多养 98 行 JS 和一条"自动播必须能停"的无障碍要求。
   现在桌面四列、手机 2×2,`site.js` 里连 `setInterval` 都不剩。这一条推翻了上面第 2 条的
   "截图轮播"、第 7 条的 lazy→eager 提升（原生网格不需要它）、第 9 条的指示点热区。
   守卫跟着换判据而不是删掉：`test_all_four_shots_are_visible_at_once` 钉"四张都在这一节
   + 不许再有轮播挂载点 + JS 里不许有定时器 + 宽屏排四列",变异验过（去掉 `repeat(4` 立刻红）。
