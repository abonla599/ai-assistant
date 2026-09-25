# 壳的三项原生能力：提醒、分享入口、桌面组件

日期：2026-09-19　状态：设计已逐节确认，待写实现计划

## 0. 背景：这个需求为什么不是"独立软件"

起因是"能不能把 app 从浏览器里独立出来，手机留一个稳定版本，点检查更新才更新"。
把现状读清楚之后，诉求换了形状：

- 现有 `android/` **已经是原生应用**（`MainActivity` + `WebView`，自己的清单、权限、签名身份）。
  通知、桌面组件、系统分享入口都属于**宿主应用**，与"页面从哪加载"无关。
- 他最终要的是**原生能力**，不是版本自主权、不是离线、不是上架。
  所以"钉版本 / 检查更新"这条明确不在本设计内（见 §8）。
- 三项能力都做得成，且**服务端只改一行**（一个响应头，§2 末尾）。

用户选的三项：通知提醒（只靠本地排期，不要服务端推事件）、系统分享入口、
桌面小组件 + 快捷入口（内容 = 今日提醒 + 快捷入口，不要学习统计）。
明确**不做**本地数据库与离线队列。

## 1. 总体形态与边界

`android/` 从 1 个 Activity 长成 9 个小类 + 3 个资源文件；`build.gradle` 里依然不出现
`implementation` —— 零第三方依赖这条性质保住，全部走平台 API。

```
android/app/src/main/java/xyz/fenever/assistant/
  MainActivity.java        改：装桥、收冷启动 extra、onPageFinished 后冲事件
  ShellBridge.java         新：唯一注入 JS 世界的对象
  NotificationChannels.java 新：一次性建渠道
  core/ReminderStore.java  新：排期表（不 import android.*，JVM 可单测）
  core/Reminder.java       新：提醒记录（不 import android.*）
  core/ShareInbox.java     新：待分享件队列（同上）
  core/MiniJson.java       新：极小 JSON 编解码（同上；见下方说明）
  PrefsIo.java             新：core 层与 SharedPreferences 之间唯一的接缝
  ReminderScheduler.java   新：往 AlarmManager 排/撤
  ReminderReceiver.java    新：到点 → 发通知 + 推进 repeat + 刷组件
  BootReceiver.java        新：开机重排（RECEIVE_BOOT_COMPLETED 是安装期权限）
  ShareActivity.java       新：ACTION_SEND 跳板，NoDisplay，自己不出界面
  AssistantWidget.java     新：AppWidgetProvider
android/app/src/main/res/layout/widget_assistant.xml   新
android/app/src/main/res/xml/widget_assistant_info.xml 新
android/app/src/main/res/xml/shortcuts.xml             新（静态快捷方式，零代码）
AndroidManifest.xml        改
build.gradle               改：versionCode 13 / versionName "0.13"
```

**每个类一个职责**，且刻意把不带 Android 依赖的逻辑收进 `core/`：`ReminderStore` 与
`ShareInbox` 能在 CI 里跑真正的 JVM 单元测试（见 §6）——这是本设计里唯一能把"写 Java
却验不了"这件事压下去的手段。

`core/MiniJson.java` 是这条理由逼出来的额外文件：`android.jar` 里的 `org.json` 是 stub，
JVM 上一 `new JSONObject()` 就抛 `"Stub!"`。用它等于让 core 层的单测跑不起来，而单测正是
这批改动唯一的本地证据。所以自己写一个只覆盖我们用得到的子集的编解码（约 100 行，含测试）。

**权限只加两项**：`POST_NOTIFICATIONS`（Android 13+ 运行时权限，用户第一次设提醒时问一次）、
`RECEIVE_BOOT_COMPLETED`（安装期，不弹框）。
**不申请** `SCHEDULE_EXACT_ALARM`，不加前台服务，不做"请到设置里把助手加入白名单"的厂商引导。
代价写死在这里：用 `setAndAllowWhileIdle`，提醒**可能晚几分钟**，Doze 深睡下更晚。
对"7:30 背词"够用，对"准点抢课"不够——本产品不承诺准点。

> **2026-09-22 修订（v0.18）**：上面这条"不申请 SCHEDULE_EXACT_ALARM"已作废，起因是用户报告
> "设过的提醒从来没响过"。现行口径：**申请** `SCHEDULE_EXACT_ALARM`（`USE_EXACT_ALARM` 仍不申请，
> 理由是它读不出真/假、拿不到授权时是静默的），拿不到授权时照旧回退 `setAndAllowWhileIdle`——
> 提醒永远设得上，只是档位不同。"不承诺准点"这句仍然保留：本产品不把准点当成交付承诺，
> 但它现在把**当前是哪一档**显示在提醒页那一行上。判据与理由见
> `docs/superpowers/plans/2026-09-22-exact-alarms-and-reminder-status.md` §1。

## 2. 桥契约

JS 世界只多一个对象 `window.AssistantShell`，八个方法，全部**同步返回 JSON 字符串**
（不引回调、不引线程模型）：

```
capabilities()                                → {"shell":1}
setOwner(user)                                → 之后到点的通知只发给这个人
scheduleReminder(json)                        → {"ok":true,"nextAt":...}
cancelReminder(id)                            → {"ok":true}
listReminders()                               → [{"id":...,"at":...,"title":...}]
pendingShares()                               → [{"id":...,"name":...,"mime":...,"size":...}]
readShareChunk(json)                          → {"b64":"..."}   {"id","offset","length"}
consumeShare(id)                              → {"ok":true}
```

> **2026-09-22 修订（v0.18）**：上面那份"八个"已经不是清单了——v0.16 加 `checkUpdate` 时它就是第九个，
> 而当时唯一的锁只判"每个名字至少出现一次"，加方法不会红，于是这句散文独自谎了两个版本。
> 现行口径：**清单不写在任何散文里**，由 `backend/tests/test_android_shell.py` 从 `ShellBridge.java`
> 的 `@JavascriptInterface` 与 `shell.js` 的调用点各扫一遍比对（`shell.js:8` 同批去掉方法数）。
> 这一版桥面上又多了两处：`capabilities()` 回包加 `notifications` / `exactAlarms` 两个键，
> `listReminders()` 每行加 `firedAt` / `missed`，并新增 `openSettings(json)`（target 白名单两种）。
> "只加不减不改语义"这条铁律不变——老壳还在人手上。

三条铁律：

1. **桥不接触会话令牌、不拼 JS、不执行任何文本。** 原生→JS 只有一个固定句式
   （`window.__shellEvent && window.__shellEvent('<json>')`），且 json 里**只放 id 不放内容**——
   分享来的文件名是外部可控字符串，拼进 `evaluateJavascript` 就是 JS 注入；JS 拿 id 回查元数据。
2. **id 走正则白名单**（`^[A-Za-z0-9_-]{8,24}$`，与 `export_store.TICKET_ID_RE` 同脾气；
   提醒 id 是 JS 给的 `r-` + 12 位，分享 id 是壳给的 16 位 url-safe 随机），
   路径永远由壳自己拼成 `cacheDir/shares/<校验过的 id>`，绝不接受外部传入的路径片段。
3. `readShareChunk` 单次 `length` 上限 512KB，超出直接拒绝；分享件消费后或 30 分钟即删。

**已拍板的两条裁决**：

- **① `owner` 命名空间**：提醒与分享件都带 `owner`（用户名，不是令牌）。不加的话，A 账号设的
  "提醒我给某人发消息"在 B 登录后照样弹到锁屏上，与已修过的跨用户记忆泄露（见
  `2026-09-18-account-switching-design.md`）是同一个形状，只是发生在设备上。
  `listReminders()` / `pendingShares()` 只返回 `owner == activeOwner` 的条目；
  `activeOwner` 由 JS 在每次登录/切号后调 `setOwner` 写入，**未 setOwner 前桥一律返回空集**
  （fail-closed，宁可看不见也不看见别人的）。
- **② 那一行 CSP**：`@JavascriptInterface` 会挂到**每个 frame** 的 window 上，而
  `view.getUrl()` 只看主文档，所以同源页面上任何能插入第三方 iframe 的 XSS 都能绕过 origin 校验。
  真正的补法不是加固校验，是给 `/app` 加响应头 `Content-Security-Policy: frame-src 'none'`，
  写在 `backend/app/web/web_router.py:53`（`RevalidatingStaticFiles.get_response` 设
  `Cache-Control` 的旁边；初稿写的 `get_file_response` 是错的方法名，那个类只覆写
  `get_response`）。**这是全设计唯一一处后端代码改动**（CI 工作流另计，见 §6）。
  可行性已实测：全站 grep 无 `iframe`、无 `window.open`，所以它不会碰坏任何东西。
  残余风险如实记录：若将来 XSS 能在**同源**页面执行 JS，桥仍可被调用；
  届时泄漏面是"用户自己设备上刚分享进来的图片"与"自己设的提醒"，不含令牌、不含会话数据。

**JS 侧降级**：`window.AssistantShell` 不存在时（浏览器直接开网址、以及 headless Edge 里跑
CDP 验收），提醒那栏显示"这里设的提醒只在这台手机的应用里生效"，分享队列恒空，其余功能照旧。
这条不是兼容性装饰——它是"同一份 JS 同时服务浏览器与 APK 两种宿主"的地基。

## 3. 提醒排期

- **存储**：`SharedPreferences` 单文件，值是一段 JSON 数组，每条
  `{id, owner, at, title, body, repeat}`。`id` 由 JS 生成（`r-` + 12 位随机），
  使 JS 成为"这条提醒是谁建的"的事实来源，壳只是执行者。
- **时间约定**：`at` 是 epoch 毫秒，由 JS 用设备本地时区算好再传；**壳不做任何日历运算**。
  不写死这一条，跨时区与夏令时时会出现两套解释。
- **repeat**：`once | daily | weekly`。`once` 触发后从表里消失；其余把 `at` 推到下一次并重排。
  推送过点（Doze 延迟）不补发历史轮次，直接对齐下一个未来时刻。
- **排期**：`AlarmManager.setAndAllowWhileIdle(RTC_WAKEUP, at, pi)`；
  `PendingIntent` 用 `FLAG_IMMUTABLE | FLAG_UPDATE_CURRENT`，requestCode 取 `id.hashCode()`。
- **上限**：每个 owner 32 条，超出返回 `{"ok":false,"error":"too_many"}`，防止 JS 把表撑爆。
- **到点**：`ReminderReceiver` → 校验 `owner == activeOwner`（不符则跳过不发）→
  发通知（channel `assistant_reminders`，`IMPORTANCE_DEFAULT`）→ 推进 repeat → 刷组件。
- **点通知回界面**：通知的 Intent 打开 `MainActivity` 带 extra `open_from=reminder:<id>`。
  页面可能还没加载完，所以事件先排队，在 `WebViewClient.onPageFinished` 之后统一冲出。
  **不改 URL**（换 query 会触发整页重载，把已登录的界面打断）。
- **重启**：`BootReceiver` 读表，把所有未来的 `at` 重排（AlarmManager 的闹钟在重启后不保留）。
  已过点的 `once` 不补发，只按 repeat 规则推到下一次。

> **2026-09-22 修订（v0.18）**：这一节里三处按原样已不成立，改的都是"让到点这件事看得见"，
> 不改排期语义（`once` 不补发、`at` 由网页算、32 条上限、requestCode 取 `id.hashCode()` 都照旧）。
> - **存储形状**：每条多两个记账字段 `firedAt` / `missed`，所以是"三个可变字段"而不是原来那句
>   "只有 `at` 可变"。读不到这两个键的旧落盘文件按 0 处理。
> - **排期**：不再固定 `setAndAllowWhileIdle`。档位由 `core/AlarmPolicy.pick()` 依
>   `canScheduleExactAlarms()` 与 SDK 版本决定，能排准点就用 `setExactAndAllowWhileIdle`，
>   否则退回原方式——**任何情况下都至少排上一档**，不因为没授权就把这条提醒丢掉。
> - **到点**：通知权限没给时不再"连表都不动地直接 return"，而是给这批到点的各记一笔 `missed`
>   后返回（记一笔 ≠ 推进排期：吞掉这条是系统替我们做的决定）。发出去的那些记一笔 `firedAt`。
>   这两笔就是提醒页那一行与"上次发出/几次没发出"的全部依据；判定只此一处（`PermissionStatus`）。

## 4. 系统分享入口

- **清单**：`ShareActivity` `exported=true` + `theme=@android:style/Theme.NoDisplay`，
  intent-filter 收 `ACTION_SEND`，`text/plain`、`image/*`、`application/pdf` 三种
  （PDF 与后端 `uploads.py` 的 doc 类对齐）。只支持单条，`SEND_MULTIPLE` 不做。
- **流程**：读 `EXTRA_STREAM` → 用 8KB 缓冲流复制进 `cacheDir/shares/<新 id>`（**不整块进内存**）
  → 记元数据 → 启动 `MainActivity` 带 extra `pending_share=<id>` → `finish()`。
  字节过河只有一条路：JS 调 `readShareChunk` 分块取 base64，拼成 Blob 后走**现有**
  `POST /v1/uploads`，拿回附件 id 再正常发消息。**服务端零改动。**
- **体积**：>10MB 直接拒（`uploads.py:27-28` 图片/文档上限就是 10MB，不另造一个数）。
- **清理**：`ShareInbox` 每次被调用时顺手删除超过 30 分钟的文件；`consumeShare` 立即删。
  `cacheDir` 系统在存储紧张时可能自己清掉——所以 JS 侧读到"文件不在了"要提示重新分享，
  而不是静默失败。
- **归属（2026-09-19 实现时改判）**：分享件带 owner。未 `setOwner` 前收到的记为 `null`（孤儿），
  **由第一个 `setOwner` 的人认领**（先到先得），30 分钟后一起消失。
  初稿写的是"孤儿对任何人不可见"，实现时否掉，因为那条会把首次使用的主路径打死：
  从相册点分享 → 壳启动 → 网页要求登录 → 登录完成 → 那张图已经谁也看不见了。
  **残余风险如实记录**：如果有人在从未登录的状态下分享，而下一个在这台设备上登录的是另一个人，
  后者会看见前者那张图。窗口 ≤30 分钟、内容限于"刚从本人相册分享出来的那一个文件"、
  且要求设备本身被两人交替使用。提醒表没有这个口子（`add` 时 owner 已确定，不存在孤儿）。

## 5. 桌面组件与快捷入口

- **`AssistantWidget`**（`AppWidgetProvider`）+ `widget_assistant.xml`：标题行 +
  最多 4 行提醒 + 底部两个按钮。**只用平台控件**（LinearLayout/TextView/ImageView），
  不用 RecyclerView，因此不引 `RemoteViewsService`。超出 4 条显示"还有 N 条"。
- **刷新**：`ReminderStore` 任何写操作后 `AppWidgetManager.updateAppWidget`；
  `onUpdate` 里按 activeOwner 重算；intent-filter 加 `ACTION_DATE_CHANGED` 与
  `ACTION_TIMEZONE_CHANGED`，让"今日"跨天时内容会换。**不做定时轮询**（AppWidget 本身有
  最小更新间隔限制，轮询是白费劲）。
- **点按（一个被证据逼出来的取舍）**：组件按钮原本最自然是"拍照提问"直接拉起相机，但那需要
  `FileProvider` 给相机一个可写 URI，而 `FileProvider` 在 `androidx.core` 里 —— **会打破零依赖**。
  取舍：按钮只做"打开 App 并告诉 JS 该弹哪个面板"（extra `open_from=camera|new_chat`），
  相机由网页的 `<input type=file capture>` 触发（`MainActivity.onShowFileChooser` 已支持，
  相机运行时权限已打通）。零新依赖，代价是少一步直达。
- **App Shortcuts**：`res/xml/shortcuts.xml` 静态快捷方式两条（拍照提问、新开对话），
  指向 `MainActivity` + 同一个 `open_from` extra。零代码、零依赖。

## 6. 测试与可验证边界（诚实账）

本机没有完整 Android SDK（跑不了 `gradle assembleDebug`），**但 `javac` 加一份 `android.jar`
就能在本地把"找不到符号"这一整类错误抓出来**——而 v0.12 连着两轮 CI 红，全是这一类
（`setUserAgent`、`allowOverwrite`，以及我"修"出来的第三个不存在的名字 `setAllowedOverwrite`）。
命令（一次性下载 61MB 平台包，产物留在仓库外）：

```bash
cd /c/Users/34426/.qoder-cn/tmp/android-platform
cp <repo>/android/app/src/main/java/xyz/fenever/assistant/MainActivity.java src/xyz/fenever/assistant/
# src/xyz/fenever/assistant/BuildConfig.java 是手写桩，只给 APP_URL
javac -encoding UTF-8 -classpath android-34-ext12/android.jar -d out \
      src/xyz/fenever/assistant/*.java
```

它抓得到：平台 API 的方法名与签名、类型不兼容、抽象方法未实现。
它抓不到：资源引用（`R.*`）、清单合并、lint、运行时行为、真机渲染。
**所以任何 Java 文件在提交前先过这条命令**，CI 只用来兜它抓不到的那部分。

分层如下：

| 层 | 在哪验 | 验什么 |
| --- | --- | --- |
| Java 平台 API 签名 | **本机 `javac` + `android.jar`** | 方法真实存在、参数类型对（上次那类错误只在这层暴露，且现在本地就能抓） |
| `core/ReminderStore`、`core/ShareInbox` | CI `gradle :app:testDebugUnitTest`（纯 JVM，不引 Robolectric） | JSON 编解码、owner 过滤、32 条上限、repeat 推进、id 正则拒绝 `../`、文件名清洗 |
| Java 整体 + 资源 + 清单 | CI `assembleDebug` | 本机 javac 覆盖不到的构建期问题 |
| JS 降级路径 | 本机 headless Edge + CDP | 无 `AssistantShell` 时不抛错、提醒栏出说明文案、`pendingShares` 恒空 |
| 桥方法名集合 | 已有 pytest 前端语料锁（`_code_lines` / `_strip_js_comments`） | 方法名漂移即红 |
| 通知真响、Doze 延迟、组件渲染、分享面板出现、重启后提醒还在 | **只能实机，由你在手机上点** | 见 §9 清单 |

新增 `.github/workflows/android-tests.yml`（`push.paths: android/**` + `workflow_dispatch`），
跑 `assembleDebug` 与 `testDebugUnitTest`。为什么单开一个文件：`release-apk.yml` 由 tag 触发，
出包前必须先有测试可跑，把两者并到一个文件会让"测试失败"和"发版失败"混成一次红。

**教训入档**：v0.12 那轮我说过一次"修好了"，依据只是"把报错的两个名字改了"，没等 CI 复跑，
结果换进去的 `setAllowedOverwrite` 同样不存在。判成败要可核验的证据——编译器说通过才算通过。

## 7. 发布与版本

- `versionCode 13` / `versionName "0.13"`；打 tag `v0.13` 触发既有 `release-apk.yml`
  （它已校验 tag 与 `versionName` 必须一致）。
- Release 说明**必须改**：现在不再"只是个壳、改服务端不用重装"。向后兼容约定写死两条：
  **桥的方法只加不减、不改语义**；**服务端与前端都不得假设壳有桥**。
  老壳装在新服务端上必须仍能正常聊天（走 §2 的降级路径）。
- 签名仍是 CI 每次现装的 debug keystore（仓库里没有 keystore，`build.gradle` 没有
  `signingConfig`，workflow 也没有持久化步骤）。推论：runner 是临时机器，
  `~/.android/debug.keystore` 的密钥对每次都重新随机，所以**跨版本大概不能覆盖安装**——
  要先卸载，卸载会清掉 WebView 的 localStorage（管理员口令、角色设定重填；聊天记录与记忆在
  服务端，重新登录就回来）。**只是推论，未实测。** 只有一个用户时不值得为它固定 keystore；
  真要给外人装之前再补（secrets 存一把 + `signingConfig` 指过去）。上架要 AAB + 永久上传密钥，
  那是另一次设计（§8）。

## 8. 明确不做

- 钉版本（把界面也锁在某一版上）。它只防"新前端带病上线"，代价是丢掉
  "重启电脑＝给所有手机热修复"这条现在白拿的性质，且服务端坏的时候钉住的旧壳照样打过去。
- ~~检查更新~~ —— **本条已于 2026-09-20 撤销，见 §10**。当时把它和"钉版本"绑在一起判断，
  而用户真正要的那半句是【安装包只在点了按钮之后才更新】，这一半与钉版本无关、也不付上面那笔代价。
- 本地数据库、离线可看历史、同步队列。
- 服务端推送通道（`/v1/tasks` 是管理员专属的 agent 子任务分解，不是 per-user 事件源，
  要做推送等于新起一个子系统）。
- 学习统计类组件内容。
- 上架 Google Play / 签名密钥迁移。
- iOS（没有 iOS 客户端，不为其设计）。

## 10. v0.15 补记：「检查更新」这颗按钮

用户 2026-09-20 的原话诉求：**"如果不点击检查更新，就无法获取最新的安装包"**。
这条与 §8 划掉的那两件事不是同一件：

| | 归谁管 | 本版做不做 |
|---|---|---|
| 聊天界面（网页）是哪一版 | 服务器；打开即最新 | 不动，也不该动 |
| 壳的原生能力是哪一版 | 手机上的 APK | **只做这一格** |

**形状**：入口是长按图标的第三条快捷方式 + 桌面组件第三颗按钮（用户明确选了"纯原生 UI"，
不在网页的设置页里放行）。点了之后在后台线程问一次
`https://api.github.com/repos/abonla599/ai-assistant/releases/latest`，判定全在
`core/ReleasePlan`（纯 Java、有 JVM 单测）：三态而不是"有/没有"，
读不出来的东西绝不报成"已经是最新版"。有新版先弹确认框，**点下载才动流量**；
DownloadManager 下完用 `getUriForDownloadedFile` 的 `content://` 起系统安装页
（不引 FileProvider，它在 androidx 里）。

**守住的三条边界**（都有锁，`backend/tests/test_android_shell.py`）：
① 没有任何自动检查——`startUpdateCheck()` 全仓只有一个调用点，且必须在用户动作的判据后面；
② 全仓只有一处提到 `api.github.com`，就在 `ReleasePlan.LATEST_URL`；
③ 起安装页与 `REQUEST_INSTALL_PACKAGES` 同批存在，少一边都是"点了没反应"。

**已知代价，用户明确表示可以承受**：这颗按钮本身要装了 v0.15 才存在。
手上是 v0.14 及更早的设备，第一次仍然得从 Release 页手动下载——自举问题，
除非引入服务端推送（那条在 §8 里已经排除）。

## 9. 实机验收清单（在手机上点，13 条）

1. 设一条 2 分钟后的提醒 → 锁屏出通知，标题正文与设置一致。
2. 通知点进去 → 直接落在助手界面，且不是重新登录。
3. 设一条每天重复 → 触发后第二天时间已推进，不是消失。
4. 手机重启 → 那条提醒还在且会响。
5. 相册里选一张图 → 分享 → 出现"AI 助手" → 点开后进 App 且附件栏已挂着那张图 → 发出去模型能看见。
6. 分享一张 >10MB 的图 → 有明确拒绝提示，不是静默没反应。
7. 桌面组件显示今日提醒；换到另一个账号登录 → 组件里看不见上一个账号的提醒。
8. 手机浏览器直接开 `https://ai.fenever.xyz/app/` → 一切照旧，提醒栏出说明文案，无报错。
9. 装 v0.13 时**先不要卸载**：覆盖成功且口令与提醒都在 → §7 的签名推论作废，以后换包零代价；
   提示签名冲突 → 推论成立，把这条结论回填 §7，并在发版说明里写清"要先卸载、口令需重填"。
   这是整份设计里唯一只能在真机上拿到的证据，别拖到下次想起来。

### 加固轮新增（v0.13 之后那批改动带出来的，只能真机看）

10. 从别的 App 分享一段**纯文本**给助手 → 附件栏出现一个小文件、内容就是那段文字。
    清单加了 `text/plain` 这条入口，而"别的 App 分享文字时到底给不给 `EXTRA_STREAM`"
    各家实现不一样，本机没有实机可证。
11. 分享一段超过 1MB 的文字 → 网页顶栏出红字「分享没收下：这段文本太长」。
    这一条同时验两件事：`Theme.NoDisplay` 那个进程里 `startActivity` 会不会被后台活动
    限制（BAL）拦掉，以及 Android 12+ 会不会把那条 Toast 掐了。两条路互不依赖，
    所以看到任意一条也算通过——但要记下来看到的是哪一条，另一条下次补。
12. 长按桌面图标 → 两条快捷方式都在且点得动。静态 XML 那条链路（`android:data` 能不能
    被系统的快捷方式解析器读出来）没有实机证据；读不出来时冷启动那次自检会补上
    Java 建的动态快捷方式，所以"能看到"不等于"走的是 XML 那条"。看的时候顺带记：
    点进去有没有落到对应界面（新建对话 / 拍照）。
13. 手动改系统日期或时区 → 桌面组件的"今日"跟着换。`android.intent.action.TIME_SET`
    这条在这一轮之前写成了 `TIME_CHANGED`（那个动作串根本不存在，过滤器永不投递），
    改对了也只证明"值写对了"，个别 ROM 会不会投这类 protected 广播仍然未知——
    所以另给了 `updatePeriodMillis=30 分钟` 与开机重排时顺手刷一次两道兜底。
