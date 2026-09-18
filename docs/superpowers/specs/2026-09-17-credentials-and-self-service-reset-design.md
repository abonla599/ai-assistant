# 凭据与自助重置：用户名 + 口令 + 三道固定找回题

日期：2026-09-17 起草（设想），2026-09-18 按已落地代码同步（as-built）
状态：已实现。**除 §2 那一节（它是 09-17 动笔当时的基线）以外**，下面各节说的都是仓库里
现在真有的东西，函数名、端点路径、常量名都能在 `backend/` 里 grep 到；与 09-17 那份设想
的差异（问题由自设改成全站固定、端点少一条、两个账本拆开、比对不短路）集中列在文末
**§11 与 2026-09-17 设想的偏差**。
取代：`2026-09-16-identity-and-admin-roles-design.md`（那份的主线是"一次性邀请码换令牌"，
该机制已在 09-17 整体删除；它的跨用户隔离与鉴权契约结论仍然有效，相关事实与遗留项已并入本文
§7、§10，故原文件删除，避免两份互相矛盾的"当前设计"同时存在）

## 1. 目标与范围

让一个跑在个人电脑上的多人助手，在**没有邮件、没有短信、没有任何外部服务**的前提下，把
"我是谁、我怎么进去、我忘了口令怎么办"三件事闭合。

范围内：

1. 注册分两步：第一步用户名 + 密码 + **确认密码**，第二步留**三道全站固定找回题**的答案
2. 新增免凭据的**自助重置口令**端点（`POST /v1/auth/reset`）与登录页的找回流程
3. 用户名重名的提示要显眼：红字挂在**用户名那一格下面**（`.field-err`）并把焦点落回那一格
   （设想里的"再给那一格加一圈红边"没有做，样式表里也没有这条规则——见文末 §11）
4. 首屏说明卡整块删除，登录层收成居中单列

范围外：设备指纹、邮箱体系、配额与计费、后端托管迁移。

## 2. 现状（已核实的代码行为，非推测）

**这一节是 2026-09-17 动笔那一刻的基线，不是现在的形状**（现在的契约看 §4）。留着它是为了
说明这轮到底改了什么；其中两处此后已经变了：免凭据面从两条变成三条（§4.5），撞名那句实话
从「该用户名已被占用」改成「该用户名已存在」（`core/auth.py` 的 `AuthStore.register`，
`tests/test_auth.py` 里专门有一条不许再挂在字面上的说明）。所以本节的引用一律给符号名，
不给行号——行号会在下一次改动里变成谎话。

| 事实 | 位置 |
|---|---|
| 口令登录：`pw_hash = "bcrypt:" + bcrypt(base64(sha256(pw)))`，先预哈希是为绕开 bcrypt 只吃前 72 字节的截断 | `core/auth.py` `hash_password` / `_prehash` |
| 一个用户可有多枚会话令牌（`tokens` 列表，上限 8，FIFO 挤出），换手机不必重新注册 | `core/auth.py` `MAX_SESSION_TOKENS` |
| `rotate_token` 清空全部令牌，且**不会**顺手解除停用 | `core/auth.py` |
| 登录失败三种原因（查无此人 / 口令错 / 已停用）对外**逐字节同一句话**，并用 `_DUMMY_PW_HASH` 均衡耗时 | `core/auth.py` `_LOGIN_FAIL` |
| 注册开放但按真实 IP 限流：失败 10 次/10 分钟、成功 3 次/24 小时；IP 取 `CF-Connecting-IP`（源站只听回环，隧道是唯一入口），`X-Forwarded-For` 明确不信 | `core/auth_router.py` |
| 重名已经是 409 +「该用户名已被占用」（这句实话此后改了词，见上），但界面只把它当一行小字提示 | `core/auth.py` `AuthStore.register`、`static/app.js` `submitAuth` |
| 管理员页有停用/启用/轮换令牌/删除，**没有任何改口令的入口** | `web/admin/admin.js`、`core/auth_router.py` |
| 免凭据路径只有 `/v1/auth/register`、`/v1/auth/login` 两条，由路由契约测试钉住 | `core/authz.py` `PUBLIC_PATHS` |

## 3. 已确认的设计决定（这一轮人拍板的六条 + 实现路上补的两条）

| # | 决定 | 取值 | 理由 / 代价 |
|---|---|---|---|
| 1 | 找回凭据何时设置 | **注册第二步必填**：全站固定三题（`RECOVERY_QUESTIONS`），每题一个答案 | 可选就等于没人填，自助重置这条路白修。设想里的"自己写一个问题"改成了固定题：问题写死在代码里，服务器就不必再"把问题念给你听"（那条端点随之删除，见 §11）。代价：注册从 2 格变 5 格、分两步 |
| 2 | 重置后是否换答案 | **可选**（`reset_password(..., new_answers=None)`），界面不提供这一格 | 人拍板。固定问题不等于固定答案，答案泄露过仍然换得掉——存储层有这条通路并有测试钉住。代价：界面上换不了，只能整号让管理员处理 |
| 3 | 重置后是否吊销既有令牌 | **吊销全部**（`record["tokens"] = []`，等价于管理员那次 `rotate-token` 的清空效果） | 会走"忘记密码"的人，多半正是怀疑自己的账号被别人用着；留着旧会话等于把门开着。代价：本人其他设备要重新登录一次。（这一条 09-17 原写"不吊销"，`81bee43c` 改判） |
| 4 | 库里现存的老账户 | 直接删，不迁移、不回填 | 人确认全是试验账户。**至今没删完**：见 §7 与 §10，那三条记录仍然在 `data/users.json` 里 |
| 5 | 重置成功要不要计入预算 | **计**：每来源每 24 小时最多 3 次成功重置（`MAX_RESETS_PER_SOURCE` / 独立账本 `_RESETS`）；**猜答案的失败也独立成册**（`_RESET_FAILS`，任何成功路径都不清它） | 猜密保答案比猜口令容易得多；只罚失败不罚成功，等于允许"慢慢试到成功为止"。拆册当年要买的是"猜答案的人不能靠随手登录成功一次把预算还给自己"——那时 `register`/`login` 成功确实会清掉共用的 `_FAILS`；终审 F3 之后**任何成功都不再还账**（§6 第 8 条），这句已经不是拆册的理由了。这层独立性仍然要留：它保证猜答案的花费与猜口令的花费互不顶包、各自有界（§6 第 3 条） |
| 6 | 后端要不要校验"两次密码一致" | **不要**，确认密码是前端的事 | `RegisterRequest` 只有 `username / password / security_answers` 三个字段。后端收一份 confirm 字段不增加任何安全属性，只多一个能填错的入口 |
| 7 | 三题是"全对"还是"对一题即可" | **必须全对**（`all(results)`） | 第二题是"你的用户名是什么？"——只要它对上就放行，这条路等于敞着的门：读得到用户名的人 100% 答得对 |
| 8 | 三条答案的比对要不要短路 | **不短路**：三枚 bcrypt 全跑完再判 | 错在第几题一旦能从耗时里读出来，三题就拆成三份互相独立的盲猜预算，整条路的强度除以三。代价：查无此人也得付满三次（用三枚 `_DUMMY_PW_HASH` 补齐），见 §4.4 |

## 4. 数据结构与端点契约

### 4.1 `data/users.json`：新增的只有 `answer_hashes` 一键

```json
"u_4f9a21c7": {
  "user_id": "u_4f9a21c7", "username": "张三", "username_lc": "张三",
  "pw_hash": "bcrypt:$2b$...", "tokens": ["sha256:..."],
  "answer_hashes": ["bcrypt:$2b$...", "bcrypt:$2b$...", "bcrypt:$2b$..."],
  "role": "user", "disabled": false, "created_at": "...", "last_used_at": "..."
}
```

- **问题文本不进库**。三题是全站常量 `app/core/auth.py:RECOVERY_QUESTIONS`
  （`("你小学在哪上？", "你的用户名是什么？", "你的父亲叫什么名字？")`），
  记录里只存三个答案的摘要，**数组下标即题号**。设想里的 `sq_q`（明文存问题）与
  `sq_hash` 都不存在，`security_question` 这个键在整个仓库里已经没有地方写了。
- 条数不写死在任何第二处：`ANSWER_COUNT = len(RECOVERY_QUESTIONS)`，加一句题面，
  形状闸门、比对次数与那句"3 题"文案自己跟上。
- 摘要与口令同构（同一个 `hash_password`：`"bcrypt:" + bcrypt(base64(sha256(key)))`）。
  **明文答案绝不落盘、不进日志、不进响应体**——它和口令是同一类东西，熵往往还更低。
- 归一化只有 `_answer_key()` 那一步：`strip().casefold()`，不做别的（不剥标点、不小写化中文），
  否则"用户以为一样"的匹配失败比严格匹配更难解释。
- 长度闸门 `ANSWER_MIN = 2` / `ANSWER_MAX = 64`，前端那几格的 `maxlength="64"` 与它对齐。
- 缺 `answer_hashes` 的记录（本轮之前注册的）自助重置**永远走不通**，见 §7。
  `AuthStore._load` 对缺键不做任何回填。

### 4.2 `POST /v1/auth/register`（免凭据，三个字段）

`{username, password, security_answers: [str, str, str]}`

- HTTP 面三条答案**必填**（`RegisterRequest.security_answers: List[str]` 没有默认值）。
  存储层 `register(..., security_answers=None)` 允许一条不给——那是历史数据与测试走的路；
  但只要给就必须满 `ANSWER_COUNT` 条，半套与少一条是同一句话。
- 不合格 → 422 并**照实说是哪一格**（"找回的 3 题答案都要填…" / "密码至少 8 位" /
  "用户名最长 24 个字符"），把"答案太短"推给"用户名不合法"会让人改错地方。
- 计费口径：422（当事人自己改得好）一律不烧账本；只有 409 撞名扣一格 `_FAILS`。
- 成功仍返回 `{token, user_id, username, role}`，注册即登录；同时记一格 `_REGISTERS`。
  **不清该来源的失败账**：这里从前还有一句"并 `_FAILS.pop(ip)` 清掉登录/注册的失败账"，
  终审 F3 把它连同登录端点那半句一起删了——现在没有任何成功能还回预算（§4.5、§6 第 8 条）。

### 4.3 `POST /v1/auth/reset`（免凭据，找回那一步）

`{username, answers: [str, str, str], new_password, new_answers?: [str, str, str]}`

- 设想里那个 `POST /v1/auth/reset-password` 与实际那个 `/v1/auth/reset` 是同一件事，
  落地时选了短的那个；`api.js` 那边的包装函数叫 `API.resetPassword(username, answers, new_password)`。
- `new_answers` 是**可选**的答案轮换（固定问题不等于固定答案）。给就必须满三条，
  少一条 → 422 且什么都不动（`test_partial_new_answers_are_rejected_without_touching_anything`）。
  登录界面不提供这一格，所以前端压根不发这个字段。
- 成功 → 200 `{"status": "password_reset"}`，**不发令牌**：让他回登录页用新口令进去。
  少一条"重置即拿到会话"的凭据发放路径。
- 成功同时**清空该用户 `tokens`**（决定 3）：这次重置之前发出去的每一枚令牌，之后一律解不出来。
  与 `rotate_token` 的区别只在于除口令（与可选的新答案）之外什么都不动——**停用状态必须原样保留**，
  别把"重置口令"变成第二次"顺手解除停用"。这条在行为上测不出来（改密要求"没被停用"），
  所以钉的是源码形状：`test_reset_password_source_never_writes_the_disabled_flag`。
- 失败对外只有一句话 `RESET_FAIL = "答案不正确"`，覆盖**四种**内部原因：查无此人 /
  这个人没留答案 / 答案错 / 账号已停用。四者同句、同形状、同耗时（§4.4），否则这个免凭据
  端点就是"哪些账号存在且设过找回"的探测器。
- 口令强度沿用注册那套（`PASSWORD_MIN = 8`、`PASSWORD_MAX = 512`），新口令与该人旧口令
  相同也接受——不做历史比对，那需要多存一份数据换不来什么。
- 计费：上面那四种 → `_RESET_FAILS` 一格（打满即 429 + `Retry-After: 600`）；
  成功 → `_RESETS` 一格；新密码或答案列表本身不合格 → 422，两本都不记。
- 必须同步进 `core/authz.py` 的 `PUBLIC_PATHS`，而
  `test_route_auth_contract.py::test_public_allowlist_is_exactly_the_bootstrap_endpoints`
  会立刻因为白名单对不上而红——那是刻意的。
- **没有 `/v1/auth/recovery` 这个端点**：三题是常量，前端自己渲染，服务器不必为一句
  抄来的话再开一条免凭据信道。它是否"真的不存在"由
  `test_the_reset_step_is_reachable_without_credentials` 顺手钉住（断它不在路由表里），
  前端侧由 `test_the_deleted_recovery_endpoint_is_gone_from_the_frontend_too` 钉住。

### 4.4 三处顺序是安全属性，不是风格

都落在 `AuthStore.reset_password` 里，注释也写在函数 docstring 上：

1. **新密码与两组答案的形状先查，再读库**。反过来时"密码太短"会变成"这一轮答案猜对了"
   的确认信号，一个免凭据端点就多了一比特可问的东西
   （`test_a_bad_new_password_is_rejected_before_the_answers_are_consulted`、
   `test_the_reset_answer_count_gate_sits_in_front_of_the_comparison`）。
2. **三条答案全部比完再判，不短路**。早退一次，"第几题猜对了"就漏进耗时里，三题于是拆成
   三份互相独立的盲猜预算（决定 8）。这条锁数的是 `bcrypt.checkpw` 的调用次数：
   错答案与"查无此人"都必须跑满三次
   （`test_answer_comparison_pays_three_bcrypts_whatever_the_answer`）。
3. **"查无此人"与"没留答案"也在 `all()` 之后才判**。放前面就是给它们开一条"秒回"的快路径；
   比对时它们各自拿三枚 `_DUMMY_PW_HASH` 补齐，条数与耗时都不随人变。
   `all(results)` 之后仍要显式判这两条：dummy 摘要理论上会被 `"timing-equalizer"` 这个
   答案撞中，只靠 `all()` 不够硬。

**耗时同形性的实测记录（本机 wall clock，bcrypt 默认 cost，30+ 用户，每支 12 次取中位数）**。
四支失败路径（答案错 / 没留答案 / 查无此人 / **已停用**）之间墙钟最大跨度：本轮重测
**12.0ms（约 2.1%）**，终审那轮量到 **2.5ms（约 0.4%）**；两次都是同一支路重复跑就能吃掉的
噪声量级。"只错第 1 题"与"只错第 3 题"本轮差 **2.3ms**——上面第 2 条那条不短路在耗时上
确实成立。**成功支路本轮 746.2ms**（终审那轮 809.7ms），比失败支路多出来的是成功路径自己
那一次 `hash_password`（装新口令；给了 `new_answers` 才是三次）加上一次 `_flush()` 落盘
（`auth.py:415-420`）。这两件事都只在**已经猜中之后**才发生，不随"哪一题对了"变，所以
它们不给攻击者多一个比特。

**裁定：不补端到端计时锁。** 个位数毫秒的跨度在这台机器上同一支路重复跑就能吃掉，一条断言
墙钟的测试在这里的**误红概率高于它新抓到的东西**；而"哪天有人把比对改成短路"这个真实回归，
第 2 条那条数 `bcrypt.checkpw` 调用次数的锁（`test_answer_comparison_pays_three_bcrypts_whatever_the_answer`）
挡得比计时硬得多、且与机器无关。上面这些数字是 as-built 记录，不是被钉的契约。

### 4.5 免凭据面恰好三条，账本四本

`core/authz.py:PUBLIC_PATHS = {"/v1/auth/register", "/v1/auth/login", "/v1/auth/reset"}`。
每条都是攻击面，各自的防线在 `core/auth_router.py`（真实来源计费）与 `core/auth.py`
（同形措辞与同形耗时）里。四本账（`_LEDGERS` 就是这份清单）：

| 账本 | 窗口 / 上限 | 记什么 | 谁会清它 |
| --- | --- | --- | --- |
| `_FAILS` | 600 秒 / 10 次 | 登录失败 + 注册撞名 | **没人清**，只有窗口过期（终审 F3 删了 register/login 成功路径上的两处 `_FAILS.pop(ip)`） |
| `_REGISTERS` | 86400 秒 / 3 次 | 注册成功 | 没人清，只有窗口过期 |
| `_RESETS` | 86400 秒 / 3 次 | 改密成功 | 同上 |
| `_RESET_FAILS` | 600 秒 / 10 次 | 改密被拒（那四种原因同一格） | **任何成功都不清**：登录成功、注册成功、改密成功都不行 |

- `/v1/auth/reset` 只看 `_RESET_FAILS` 与 `_RESETS` 这两本，`_FAILS` 它**既不看不写**。
  为什么猜答案的失败要单独一册——终审 F3 之后站得住的是两条：① 猜口令与猜三题是两种不同的
  猜测面，共用一格预算时，一个人猜错九次之后连自己的密码都不许再试一次；② 429 落在哪本账上
  要看得出来（`Retry-After` 是 600 还是 86400），否则运维分不清刚被挡的是枚举用户名还是在猜
  找回答案。当初那条**首要**理由——"`_FAILS` 会被一次成功登录或注册清空，而猜三题的人随手就能
  拿到一次那样的成功"——已经随 F3 删掉那两处 pop 一起消失了，现在两本失败账都是任何成功不还。
  判据：`test_no_other_success_pays_off_the_guessing_ledger`、
  `test_a_successful_reset_leaves_the_guessing_ledger_alone`。
- `_prune` 在来源数超过 `MAX_TRACKED_SOURCES = 4096` 时按**各本自己的窗口**丢整桶过期的键。
  拿十分钟那把尺子去过 24 小时那两本，就是压力下悄悄放宽配额——一条 fail-open
  （`test_prune_uses_each_ledgers_own_window`、`test_the_prune_sweeps_all_four_ledgers`）。
- 来源键取自 `_client_ip()`：先 `CF-Connecting-IP`，取不到才退 `request.client.host`；
  `X-Forwarded-For` 刻意不信（`test_x_forwarded_for_cannot_buy_a_fresh_budget`）。
- 形状错（整个字段缺失、类型不对）落在 FastAPI 的 `RequestValidationError` 上，由
  `app/main.py:on_malformed_body` 统一压成一句中文
  `MALFORMED_BODY_DETAIL = "提交的内容不完整或格式不对：请检查每一栏都填了"`，状态码仍是 422；
  原始错误的**字段路径**只打到服务端日志（`loc`），提交值（`input`，过的正是密码与答案）
  既不进日志也不进响应体。代价见 §6 第 7 条（第 5 条是共享出口，两回事）。注意这一层只管
  **"整个字段缺失 / 类型不对"**：条数不对（交了两条答案）是一枚合法的 `List[str]`，
  由存储层 `_check_answer_shapes` 拦，回的是指着题数的那句具体话，不是这里这一句（§6 第 7 条）。

### 4.6 管理员侧不动

`/v1/admin/users/*` 保持现有的停用 / 启用 / 轮换令牌 / 删除四种，**没有任何改别人口令的入口**：
本轮已有自助重置这条路，多一个能改别人凭据的口子就多一处必须审计的写路径。
"不能替别人设凭据"这件事在端点面上由 `test_no_endpoint_can_grant_the_admin_role` 与
`test_admin_surface_never_settles_for_mere_identity` 钉着。

## 5. 首屏凭据层（最终形态）

一张弹层里换字段，共五个屏幕：登录 1 屏、注册 2 屏、找回 2 屏，全在
`static/index.html` 的 `#authModal` 里。"哪些格子在场 + 主按钮叫什么"只有两处在算，
各自管一侧：`renderRegister()` 管登录/注册那一块（`#authForm`），`renderRecovery()` 管
找回那一块（`#recoverForm`）；`showAuth()` 与 `setAuthMode()` 负责进出与换模式（§5 末尾）。

**注册第一步**（`authMode === "register"` 且 `regStep === 1`）：

```
        [图标] AI 助手
   注册分两步：先定用户名和密码，下一步留三道找回题的答案。   ← #authSub，随模式变
  ┌ 请输入用户名 ─────────────────┐   ← #authUser，两步都留在屏上
  ┌ 请输入密码            (眼睛) ┐   ← #authPass，注册时 placeholder 换成"请设置密码，至少 8 位"
  ┌ 再输入一遍密码 ──────────────┐   ← #authPass2，只在注册第一步出现
  该用户名已存在                    ← #authUserErr，就地红字（.field-err），不是末尾那行
  ┌────────── 下一步 ───────────┐   ← #authGo
  已有账号？去登录                    ← #authSwitch（登录态下写「立即注册」）
  提示行（错误红字 / 进度灰字）        ← #authHint
```

**注册第二步**（`regStep === 2`，`#regStep2` 摘掉 hidden）：密码两格收起，**用户名那一格
留着**（撞名的红字要有落点），铺出三道题与三个答案格（`#regQ1..3` 的标签文字由
`renderRecoveryQuestions()` 从 `RECOVERY_QUESTIONS` 渲染，HTML 里没有第二份题面），
副标题是那句"最后一步：这三道题将来用于忘记密码时自助找回……只存这台机器上的摘要，
管理员也看不到原话"，主按钮变成「注册并登录」，旁边一个「上一步」（`#regBack`）。

**找回**（点「忘记密码」→ `showAuthView("recover")`，同层换另一张表单 `#recoverForm`）：
第一步只有用户名一格 +「下一步」，副标题「第 1 步：先输入你的用户名。」；
第二步一次铺开三道固定题 + 三个答案 + 新密码 + 确认新密码（`#rcAns1..3` / `#rcNew` /
`#rcNew2`），用户名那一格 `disabled`（名字进第二步就定死了），主按钮「改密码并去登录」，
旁边「回去登录」（`#rcBack`）。

- **"三步"这个说法统一成这样**：用户名 → 三题三答案 + 新密码 → 回登录。
  前两步是两个输入屏幕，**第三个"步"是提交成功之后回到登录那一屏**，不是第三个输入屏。
  计划文档 Goal 行那句"三步走"如果读成"三个屏幕"就与代码不符了：代码里 `rcStep` 只有
  1 和 2 两个取值，答案与新密码永远同一次提交。
- **为什么答案不单独发一次服务器**：那会当场造出一个"这个答案对不对"的 **oracle**——
  免凭据信道上任何能单独回答"答案对不对"的端点，都等于把三题拆成三次独立预算的盲猜，
  而且顺带把"这个人存在吗"也报出去了。所以答案是**和新密码一次**交给
  `POST /v1/auth/reset`：要么整段成功，要么整段拿同一句"答案不正确"。
  界面文案也跟着说准：「三道全对才改得动：题目是全站固定的那三道，答案是注册第二步留的那三句」。
- **说明卡整块删除**：`.auth-side`、`.auth-host`、`#authHost`、`showAuth()` 里填域名那句、
  以及钉它们的 CSS 断言一起走（`test_auth_layer_is_one_column_with_no_dead_rules` 反向钉着
  这些 id/class 不许回来）。两列随之变成单列居中（`max-width: 420px`），窄屏不再有塌陷分支。
- 第一步的「下一步」**一个字节都不发出去**：`regNext()` 与 `rcNext()` 的函数体里
  一个 `API.` 都没有（`test_the_first_step_of_both_flows_asks_nothing_of_the_server`）。
  此刻答案还空着，发出去只是白挨一次 422。
- 重名：409 时把后端那句实话（`该用户名已存在`）**原样**写在用户名那一格下面，
  并把焦点落回那一格，同时不把表单末尾那行一起染红——两句话同屏时人会先去改密码
  （`setUserError()`，`test_a_taken_username_still_lands_under_the_username_field`）。
  设想里的"再给那一格加一圈红边"没有做：样式表里没有任何给 `#authUser` 上红边的规则，
  红字 + 焦点已经够指到地方，多一处形状就多一处要和暗色主题对账的东西。
- 确认密码两次不一致：**不发请求**，第一步就提示"两次输入的密码不一样"。省一次 422 往返，
  也不白烧预算。后端没有 confirm 字段（决定 6）。
- 改密成功：`showAuth("login")` 切回登录、用户名预填、格子清干净，提示行写
  **「密码已重置，请用新密码登录。其他设备需要重新登录一次。」**
  后半句是决定 3 的直接后果，藏起来只会让人以为别的设备坏了；而那句好消息必须写在
  `showAuth` **之后**，否则上一次登录失败留下的 `.err` 红色会把它显示成失败
  （`test_going_back_a_step_leaves_no_orphan_message` 钉的就是这个先后）。
- 眼睛按钮只翻首屏这一格的 `type`，不清空已输入的值。
- 进出这一层（换模式、回去登录、被 `needsAuth` 重新挡屏）都会把十个凭据格子清一次
  （`clearAuthCredentials()` ← `setAuthMode()`）；登录失败**故意不清**，密码留在框里，
  因为逼人重敲一遍只会把人赶去用 "12345678"。

## 6. 安全权衡：为什么这条路比口令弱，以及我们认了什么

必须写在明面上，否则它会以"有找回密码功能"的面貌被当成一件安全增强。

1. **找回答案是可猜、可社工的凭据**，熵通常远低于 8 位以上口令。三题的文本现在是全站
   公开的常量（写在 `app/core/auth.py` 与 `static/app.js` 里，任何人都能读到），
   所以**答案的实际熵就是那几句话的熵**——别用城市名、别用姓氏之外的常见词、
   别用"你小学在哪上"的官方答案。唯一能压住它的是 §4.5 那四本按真实来源计的账。
2. **三题里"你的用户名是什么？"这一题熵为零**：知道某人用户名的人，这一格 100% 答得对。
   它没有带来任何强度，只是让人多填一格；整条路之所以还没塌，全靠决定 7 那条
   "三题必须全对"。真要提强度就是把这一题换掉（换成一题有熵的），代价是已经注册过的
   人对不上新题面——所以它得连同库里那批账号一起处理，不是顺手改文案。
3. **两本失败账拆开之后，同一来源每个 10 分钟窗口能烧的猜测次数翻倍**：`_FAILS` 10 次
   用来猜口令/撞名，`_RESET_FAILS` 另外 10 次专门用来猜三题答案，谁也不占谁的额度。
   连带丢掉一层**意外**防护：三端点共用 `_FAILS` 那阵子，一个人在找回上猜错会把他的登录
   预算一起扣掉，于是撞名枚举也被顺手挡了一道；现在 `/v1/auth/recovery` 没了、猜答案的账
   独立了，撞名枚举回到"只有 `_FAILS` 那一本在挡"。这是拆账的代价。至于拆账当年要买的
   那件东西——"猜答案的人不能靠随手登录成功一次把预算还给自己"——终审 F3 之后它不再靠
   拆账来保证，而是两本失败账共同的口径：**任何成功都不还回预算**（见第 8 条）。
4. **重置吊销全部令牌**（决定 3）反过来带来两点：本人其他设备会被踢下线，需要重新登录一次；
   而"能猜中找回答案的人"因此获得一次可用的骚扰能力——他能让对方所有设备掉线（但他也能顺手
   改口令，所以这没有新开攻击面，只是把已有的破坏变得可见）。真正的撤销手段仍然是管理员的
   `rotate-token` / 停用 —— 这条得在 `/admin` 页面上看得出来。
5. **校园网 NAT 后共享一个出口 IP**：同宿舍几个人一起注册会撞 3 次/24 小时的成功预算；
   本轮把同一形状复制到重置预算上（`_RESETS` 也是 3 次/24 小时），于是同出口下"改密码自救"
   也会互相挤。按 IP 计费的必然代价，别指望它公平（部署文档里那条"这把锁挡得住什么、
   挡不住什么"是同一件事的运维版本）。
6. **老账户没有 `answer_hashes`，因此永远走不通自助重置**。它们不会被回填（§7），
   只会被 §4.3 那句合并文案统一挡在"答案不正确"里。**这一条不是理论值**：库里那三条
   试验账号至今还在（决定 4 没做完），所以这个分支既活在测试里、也活在真实库里，
   直到人在 `/admin` 把它们删掉。
7. **422 有两层，不是全站同一句话**（本条从前写作"422 现在全站只有同一句中文"，并把
   "条数不对"也算进那一句——两句都是错的，终审 F5 改准；错的版本还与本 spec §4.2 自己列出的
   三条具体 422 文案直接打架）：
   - **第一层：请求体在 pydantic 那一层就读不通** —— 整个字段缺失、类型不对（`answers`
     传成了字符串）、body 不是对象。落在 FastAPI 的 `RequestValidationError` 上，由
     `app/main.py:on_malformed_body`（`main.py:152-160`）统一压成一句
     `MALFORMED_BODY_DETAIL = "提交的内容不完整或格式不对：请检查每一栏都填了"`（`main.py:149`）。
     好处仍是单一来源在服务端：两个客户端（`api.js`、`admin.js`）都不必各自兜一层，`curl`
     与桌面壳看到的是同一句话。**只有这一层**把手滑的字段名扣下来不给客户端，排障时只能看
     服务端日志那行 `⚠️ 请求体形状不对（body.messages.0）`。
   - **第二层：读得通、但值不合格** —— 由存储层 `app/core/auth.py` 的形状闸门说，它跑在
     读库与比对**之前**（§4.4 第 1 条），回的是指着那一格的具体话：条数不是三条（或少一条、
     多一条）→ `_check_answer_shapes` 那句「找回的 3 题答案都要填，且每题至少 2 个字符」
     （`auth.py:142-144`）；单条太短/太长 → 「答案至少 2 个字符」/「答案最长 64 个字符」
     （`auth.py:128-131`）；口令与用户名同理（「密码至少 8 位」「用户名最长 24 个字符」，
     `auth.py:270/283`）。**"条数不对"归的是这一层**，不是上面那一句：`ResetRequest.answers`
     声明的是 `List[str]`（`auth_router.py:204`），pydantic 只判类型不判条数，两条答案是一枚
     合法的数组，闸门在存储层。
   两层的代价都是当事人自己改得好，所以**都不烧任何账本**。判据：
   `test_a_malformed_body_says_one_plain_chinese_sentence`（第一层那一句，并钉住回显里不许
   带提交的内容）、`test_a_wrong_answer_count_is_a_free_typo`（第二层那句指着题数的话，
   断言里就有 `detail != RESET_FAIL`）、`test_the_reset_billing_follows_the_flag_not_the_wording`
   的后半（单条太短那句 422 也不许被计进预算）。
   还剩一条没被这条取舍覆盖的：非 auth 端点的字段名（例如 `/v1/memory/search` 的 `top_k`
   越界、会话消息里的非对象项）在第一层不回给客户端。目前 `api.js` 自己夹了 `top_k`，
   没有现存需求被这条挡掉；但哪天有客户端要按字段做分支，这里得再议。
8. **没有任何一次成功还能还回预算**（终审 F3 删掉了 `register` / `login` 成功路径上的两处
   `_FAILS.pop(ip)`；`_RESET_FAILS` 从拆账那天起本来就是这个口径）。代价直说：**同一个十分钟
   窗口里连续打错十次的真人要等窗口自己过去**，中间登录成功也不会提前解锁，话术是
   "尝试次数过多，请稍后再试"（`Retry-After: 600`），校园网/运营商共享出口下更容易撞到。
   为什么还是删：旧契约"密码对了就说明来路正当"在产品上是假的——一台机器、一枚来源 IP，
   只要穿插"自己的号成功一次"，撞名枚举实测跑到 **45 次 409 / 0 次 429**、口令猜测跑到
   **36 次错 / 0 次 429**。"成功"在这几个免凭据端点上是随手可得的（猜对自己的口令、再注册的
   一个小号），所以那两行 pop 保护的不是真人，是脚本。删掉之后 `_register_error` 里
   "脚本要每 10 次换一枚真实访客 IP"这句才第一次真的成立。跨端点硬顶（第五本账）本轮不做。
   判据是 `backend/tests/test_auth_endpoints.py` 里那两条**反转过的**锁：
   `test_a_correct_login_does_not_pay_back_the_failure_budget`、
   `test_a_successful_register_does_not_pay_back_the_failure_budget`。

结论：对"发给朋友用的小工具"这个定位，1–5、7 与 8 是认了的代价；第 6 条不是取舍，是
一件还没做完的运维动作（§7）。要对外扩大使用范围时，找回这条路应当整块撤下，
换回"管理员重置"这一条唯一能审计的路。

## 7. 迁移（as-built：一条数据搬运都没有）

- 库里那三条试验账号（`data/users.json` 里的 `老李`、`实验1号`、`实验一号`）**至今还在**。
  决定 4 说的是"直接删、不回填"，而"删"这个动作由人在 `/admin` 页上点，本轮不替他做：
  脚本去删真实用户数据是一次不可回滚的写，收益只有"库里干净一点"。这条已作为运维项写进
  `docs/安装部署指南.md`「管理员日常四条」那一节。
  三条记录都没有 `answer_hashes`（其中两条连 `pw_hash` 与 `tokens` 都没有，只有更早那套的
  `token_hash`/`invite_code`，而今天的 `AuthStore.resolve()` 只读 `tokens` 列表），
  所以它们既走不通自助重置、也登不进来。
- 新注册用户天然带 `answer_hashes`（HTTP 面必填，§4.2）；`AuthStore._load` 对缺键
  **不做任何回填**，读到的就是"没留找回答案"，由 §4.3 那句合并文案统一挡掉。
- 无 `.bak`、无迁移日志：本轮没有任何一条数据搬运，写"迁移"反而制造假风险。

## 8. 测试矩阵（这一轮真加了什么）

**存储层**（`tests/test_auth.py`）
三题题面逐字钉住（`test_the_three_fixed_questions_are_pinned_verbatim`，含 `ANSWER_COUNT == 3`）；
答案只存 bcrypt 摘要、明文不进文件；`_answer_key` 的 `strip().casefold()` 生效；
注册"要么满三条要么一条不给"；三题全对才放行、错任意一题同一句话；
比对付满三次 bcrypt（错答案与查无此人都一样，`test_answer_comparison_pays_three_bcrypts_whatever_the_answer`）；
条数闸门在比对之前（一次 bcrypt 都不许跑）；新密码形状先于答案
（`test_a_bad_new_password_is_rejected_before_the_answers_are_consulted`）；
答案可轮换（`test_answers_can_be_rotated_at_reset`）与半套轮换什么都不动；
重置成功清空 `tokens` 且旧令牌立刻解不出来（决定 3）；**同时不得解除 `disabled`**——
行为上测不到，所以钉源码形状（`test_reset_password_source_never_writes_the_disabled_flag`），
而那把尺子自己也有判据（`test_the_structural_ruler_ignores_trailing_comments`）。

**端点层**（`tests/test_auth_endpoints.py`）
注册少一条答案是 422 且不烧预算（`free_typo` 那两条）；`/v1/auth/recovery` 不在路由表里；
猜对一题与全错逐字节同一句话；猜答案的失败进 `_RESET_FAILS`，登录/注册/改密三种成功都清不到它
（`test_no_other_success_pays_off_the_guessing_ledger`）；同一来源第 4 次成功重置 429 且带
`Retry-After`；`_prune` 扫四本账且各按自己的窗口
（`test_the_prune_sweeps_all_four_ledgers`、`test_prune_uses_each_ledgers_own_window`）；
形状错的请求体只回那一句中文（`test_a_malformed_body_says_one_plain_chinese_sentence`）。

**契约层**（`tests/test_route_auth_contract.py`）
`PUBLIC_PATHS` 恰好是 register / login / reset 三条，多一条少一条都红；
免凭据那一步真可达（含带斜杠与多一段子路径都不许白拿通道）；
`/v1/auth/recovery` 断它**不在**路由表里。

**界面层**（`tests/test_web_pwa.py`，读 JS/HTML 文本当锁）
前后端那三句题面逐字相同、且 HTML 里零副本；答案格数由后端 `ANSWER_COUNT` 推导并且
可达性（在 `#regStep2` / `#rcStep2` 容器内部、自身与祖先都不带 hidden）一起断；
两个第一步的函数体里一个 `API.` 都没有；步骤闸门是字面短路形状 `{ goNext(); return; }`
且紧跟在途闸门（中间只许无副作用的 `const`/`let` 声明与注释）；
`API.recovery` 与 `/v1/auth/recovery` 在前端不许出现（反向锁）；
确认新密码不一致时不发请求；成功提示里那句"其他设备需要重新登录一次"不许省；
撞名走 `setUserError()`（就地红字 + 焦点在用户名那一格）；
进出这一层清十个凭据格子、且连接线（`showAuth` → `setAuthMode`、`authSwitch` → `setAuthMode`）
**判的是剥掉注释之后的语句形状**；后台 401 在弹层已开时只更新状态条、不重走 `showAuth`。

**实机**：源码隔离实例（临时数据目录、假上游、不碰真实密钥）跑一遍
注册两步 → 登出 → 忘记密码两步 → 新口令登录 → 旧令牌解不出来；宽屏与 724px 窄屏各截图。
（Task 3 已跑过两轮，见 `.superpowers/sdd/2026-09-18-fixed-recovery-questions/task-3-report.md`。）

## 9. 影响面（这轮真的动了哪些文件）

`core/auth.py`（`RECOVERY_QUESTIONS`/`ANSWER_COUNT`/`_answer_key`/`_check_answer_shapes`、
`register(security_answers=)`、`reset_password(answers, new_password, new_answers=)`；
**删掉** `recovery_question()` 与 `security_question` 那套形状）、
`core/auth_router.py`（`RegisterRequest`/`ResetRequest`、`/v1/auth/reset`、
`_RESETS` 与 `_RESET_FAILS` 两本新账与 `_LEDGERS`、`_register_error` 的计费口径；
**删掉** `recovery` 端点与 `RecoveryRequest`）、`core/authz.py`（`PUBLIC_PATHS` 三条）、
`app/main.py`（`on_malformed_body` 与 `MALFORMED_BODY_DETAIL`）、
`web/static/index.html`（两步注册的 `#regStep2`、找回第二步的 `#rcStep2`、
删掉 `authQuestion`/`authAnswer`/`rcQuestion*`/`rcAnswerRow` 那套单题形状）、
`web/static/app.js`（前端那份 `RECOVERY_QUESTIONS`、`regNext`/`rcNext`/`renderRegister`/
`renderRecovery`/`clearAuthCredentials`、`setAuthMode` 收口、`needsAuth` 的第三种情况）、
`web/static/api.js`（`register(username, password, security_answers)`、
`resetPassword(username, answers, new_password)`，**删掉** `recovery`）、
`web/static/style.css`（`.auth-step`/`.auth-qa`/`.auth-q`，删 `.auth-extra` 与
`input[readonly]` 那条规则）、`tests/conftest.py`（`_isolated_throttle` 从 `_LEDGERS` 现取账本清单）
以及上述四个测试文件。文档侧：`docs/用户手册.md`、`docs/安装部署指南.md` 与本文件。
APK 壳与 EXE 结构不变（界面由服务端下发），但**需重建 EXE 才能带上静态文件**——
那一连同实机验收由人在会话里做，本轮没有重建、没有重启服务、没有推送。

## 10. 遗留

- `/admin` 页面仍然只在"轮换令牌"那一行体现撤销能力，**没有**一句"用户自己能改密、
  改完全部设备会掉线"的提示（`web/admin/admin.js` 里连"重置/自助/忘"这些字样都搜不到）。
  这一条从 09-17 挂到现在，本轮也没做：它是管理员唯一会误判"我是不是得替他改密码"的地方。
- 若将来要扩大受众：撤下找回这条路，改为管理员重置 + 一次性恢复码（§6 的结论）。
- 库里那三条试验账号待删（§7，运维项已进安装文档）。
- `sw.js` 的缓存版本号仍是 `ai-assistant-shell-v3`：外壳是 network-first、且服务端对
  `.css/.js` 自己发 `no-cache` + ETag（有测试钉着），所以不 bump 也能拿到新的 `app.js`；
  真要立即刷新，就在下次重建 EXE 时顺手 bump 一次。
- 09-16 那份文档记的两条非阻塞遗留**核对过，仍在**：
  `session/session_store.py:_load` 顶层能 parse 但不是对象时只是什么都不做（`auth.py` 那条
  后来补了 quarantine，会话这条没有——于是它可能以空表启动并在下一次写入时覆盖掉真实数据）；
  `.corrupt` 用固定名，二次损坏会吃掉上一份留证（`auth.py`、`providers.py`、`uploads.py`、
  `session_store.py` 四处同一形状）。

## 11. 与 2026-09-17 设想的偏差

这一节是给下一次读这份文档的人省事的：下面每一条都是"设想 ≠ 落地"，左边是当时写的，
右边是仓库里现在真有的。**为什么走另一条路**那一列才是重点——它们不是随手改的，
三条主线是：不给免凭据信道留 oracle、不让耗时把进度漏出去、不让一本账被别的端点清掉。

| # | 设想（09-17） | 落地（as-built） | 为什么走另一条路 |
|---|---|---|---|
| 1 | 用户自设一个问题（`sq_q` 明文入库 + `sq_hash`） | 全站固定三题 `RECOVERY_QUESTIONS`，记录里只有 `answer_hashes`（三条摘要，**问题不入库**） | 问题文本一旦是常量，"问服务器要问题"就没有任何信息量，反而多开一条信道（见下一行）；而且不入库就少一份"每个人被问了什么"的明文 |
| 2 | 免凭据面 4 条：register / login / recovery / reset | **3 条**：`/v1/auth/register`、`/v1/auth/login`、`/v1/auth/reset` | `/v1/auth/recovery` 的作用是把那个人的问题念给他听。它按用户名回答，就必然能回答"这个用户名存在吗 / 他设过找回吗"——这正是 §4.3 花力气均衡掉的那一比特。问题已是常量：整条端点删掉，**oracle 不存在了，攻击面少一条** |
| 3 | 答案与新密码分两步验 | 一次提交（界面第二步同时收三题答案与新密码） | 任何"先验答案、答案对再让你设新密码"的形状都是一台能回答"这一句对不对"的机器：它把"三题全对"这一道闸门拆成三次可单独验证的小闸门，猜中一题就少一题可猜——决定 7 与决定 8 挡的东西当场作废，而它仍然受同一本 per-IP 预算管，所以看起来"什么都没坏"。§5 那条 bullet 说的是同一件事 |
| 4 | 猜答案的失败与登录/注册共用 `_FAILS` | 独立一本 `_RESET_FAILS`（同窗口、同上界），且**任何成功路径都不清它** | 写下这条时 `_FAILS` 的契约还是"该来源成功登录/注册一次就把失败账清零"，猜三题的人随手就能拿到一次那样的成功，于是每 10 次里蒙中一次他就永远限不住——**账本洗白**。终审 F3 把那两处 pop 删了，这条首要理由随之下线（§4.5）；现在留着的是"两种猜测面各记各的"与"429 要说清挡的是哪一本"。代价见 §6 第 3 条 |
| 5 | 比对顺序没作要求 | 新密码与两组答案的**形状先查**、三条 bcrypt **全算完再判**（不短路）、"查无此人/没留答案"也在 `all()` 之后才判，三枚 dummy 摘要补齐耗时 | 两条都是计时侧信道：短路会把"第几题猜对了"漏出去（三题除以三）；"密码太短"若在答案之后判，它就变成"这一轮答案猜对了"的确认信号。**短路计时**与 oracle 是同一类洞 |
| 6 | 重置端点叫 `/v1/auth/reset-password`，成功 `{"status": "reset"}` | `/v1/auth/reset`，成功 `{"status": "password_reset"}` | 名字短一个词、状态值说全是一回事；记在这里是因为它是**契约**，写错一个字母 `test_route_auth_contract.py` 就红 |
| 7 | 重置后"不吊销既有令牌" | 吊销全部（`record["tokens"] = []`），且不得顺手解除 `disabled` | 人改判，`81bee43c` 已记：会点"忘记密码"的人多半正怀疑账号被别人用着，留旧会话等于把门开着 |
| 8 | 成功重置计预算这件事只在决定 5 里写着 | 落地为 `_RESETS`（3 次/24 小时），且 `_prune` 按各本账自己的窗口扫 | 只罚失败不罚成功等于允许"慢慢试到成功为止"。顺带修掉一条 fail-open：拿十分钟的尺子去过 24 小时那两本，压力下配额会被悄悄放宽 |
| 9 | 422 由各客户端各自兜一层（设想里没有这一条） | **只在那一层读不通时才统一**：pydantic 的 `RequestValidationError`（整个字段缺失、类型不对）由服务端一个 handler 压成一句中文、字段路径只进服务端日志；读得通但值不合格仍由存储层说指着那一格的具体话，**"条数不对"属于后者** | 形状有 N 个事实来源时，`curl` 与桌面壳这两个没兜的照样看不懂（`new Error(detail)` 会显示成 `[object Object]`）。本行从前写作"422 全站只回同一句中文"，那是错的（终审 F5）：它把 §4.2 自己列出的三条具体 422 挤掉了。代价：非 auth 端点的字段名不回客户端，两层的分法与判据见 §6 第 7 条 |
| 10 | 撞名时"给用户名那一格加红边" | 只有就地红字（`.field-err`）+ 焦点，样式表里没有那条红边规则 | 红字已经挂在指得到地方的位置；多加一处形状就多一处要和暗色主题对账的东西，而它买到的只是"再显眼一点" |
