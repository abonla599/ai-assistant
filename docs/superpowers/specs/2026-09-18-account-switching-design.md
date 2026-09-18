# 切换账户（本机多身份清单）· 设计文档（最终版）

日期：2026-09-18　状态：**已实现**（后端 `3d70cdde`；前端本机清单与切换随后）
前置：`2026-09-18-settings-redesign-design.md`（**先做那份**，本份的入口落在它的新「账户」组里）

## 1. 需求与四条已定决定

需求原话：「添加一个切换账户的界面」+「我希望切换账号之后，账号之间的记忆不共享」。

| 决定 | 用户的选择 | 代价 |
|---|---|---|
| 切换语义 | **来回切，不重输密码**（本机存多份令牌） | 见 §7 的信任模型 |
| 切换要不要再验凭据 | **不验，直接切** | 清单上任何人 = 任何人 |
| 切走时正在流式输出的回答 | **直接掐断，不弹提示** | 那半截回答不落盘（与今天刷新页面完全一致） |
| 上一个人没发出去的东西 | **一律清掉，不留草稿** | 切一次丢一次输入 |

## 2. 现状（实测，决定了这份文档的重心在哪）

**服务端已经按人隔离，不需要新做**：

- 检索按人过滤：`memory_manager.py:222 / 280 / 388` 三处都是 `where={"user_id": user_id}`——不是"存时记了归属"，是"取时按人筛"
- 对话注入：`pipeline.py:82` `search_memory(self.user_id, query, top_k=3)`
- 会话：每条带 `owner`，`list_summaries(owner)` 过滤（`session_store.py:142`）
- 偏好摘要：`preference-<uid>.txt`，一人一份
- 写入归属：`metadata` 里塞 `user_id` 的口子已堵（`IDENTITY_METADATA_KEYS`），身份键排在客户端 dict 展开**之后**（见 [[ai-assistant-memory-cross-user-leak]]）

**没做的、而且才是用户会看见的那一半**：本机。今天压根没有退出——`auth_router` 只有 register/login/reset/me + 5 条 admin，**没有 logout**；删掉 `localStorage.accessToken` 之后服务端那枚令牌**仍然有效**。而 `restore()` 第一行是 `if (!pref.sessionId) return;`，`sessionId` 是全机一个键，切到 B 之后它**还是 A 的那条会话 id**——今天不出事纯属服务端按 owner 回 404 挡住了。**这条不能靠 404 兜**，必须显式清。

## 3. 本机形状（方案一，已确认）

localStorage 新增两个键：

```
identities  = [{userId, username, role, token, lastSessionId, providerId, addedAt}, …]
currentId   = 其中一个人的 userId
```

`accessToken` / `userId` / `sessionId` 三个老键**不再存在**，改为"当前那一条"的派生只读视图：

- `pref.token` / `pref.userId` / `pref.sessionId` → getter，读 `identities[currentId]`
- 写只有三个入口：`addIdentity(res)`（注册/登录成功，**并且立即把他设为当前身份**）、`setCurrent(userId)`、`dropIdentity(userId)`
- **启动时的兜底**：`currentId` 缺失、或指向一个已经不在清单里的人 → 取 `addedAt` 最新的那一条当当前身份；清单为空才走首层注册。这条必须显式写，否则"记着 5 个人但开 app 说没登录"会有两种实现
- **跟机器**：`theme`、`contextWindow`　**跟人**：`token`、`userId`、`sessionId`、`providerId`
  （`providerId` 跟人这条是用户排期的"自带 API 模型"逼出来的：一旦每人挂自己的 key，"我在用哪个模型"就是那个人的一部分，留在机器级会出现"切到 B、请求发给 A 的模型服务"）
- `persona:<sessionId>` 不动——会话 id 全局唯一，键名自带归属
- 上限 **5 人**，超出顶掉最久未用的一条，并顺手撤销它服务端那枚令牌

两个附带动作：

1. **`api.js:13` 改成读 `pref.token`**。它现在绕过 `pref` 直接 `localStorage.getItem("accessToken")`，留着就是第二个事实来源，会出现"界面是 B、请求头是 A"。
2. **一次迁移**放在 `boot()` 最开头（在 `showAuthPending()` 之前）：有 `accessToken + userId` 且没有 `identities` → 打包成清单第一条并删掉老键。

## 4. 切换时序

```
switchTo(B):
  1. 掐断当前流（state.controller.abort()），不弹提示
  2. setCurrent(B) —— 令牌与身份指针一次换掉
  3. 清本机视图（§5 的 7 项）
  4. sessionId 换成 B 自己的 lastSessionId；没有就新建一条
  5. 重跑 loadWho() + loadServerData()（这两处自带擦状态条，见 aa1870ad）
  6. B 的令牌已被撤销 → 401 → 弹首层登录面，清单里 B 那条标「需要重新登录」
     刻意**不**悄悄退回 A —— 那会让人以为自己是 B
```

## 5. 切换时必须清的 6 项

`state.messages`、`state.sessions`、记忆面板（`state.memoryQuery` + `#memoryList`）、`#personaInput` 草稿、输入框正文 + `state.pending` 待发送附件、图片 URL 水合缓存。

**`lastSessionId` 刻意不在名单里。** 本节第一版把它列成第 7 项"必须清"，浏览器实测立刻打脸：切回上一个人时 `messages=0`——那段对话还在服务端，只是再也没人记得回去。指针归各自那条记录所有，`setCurrent(B)` 之后 `pref.sessionId` 读到的本来就是 B 自己那条，不需要也不该顺手擦掉 A 的。锁已经按反向写（`test_switching_clears_the_view_before_it_refills_it` 里断言 `lastSessionId` **不在**清屏函数里）。

**这 6 项必须一次列全并由一个函数负责**（`resetViewForIdentity()`），而不是散在切换路径里各清各的——漏一项就是"界面写着 B、屏幕上画着 A 的对话"。`afterAuth`（注册/登录成功）同样是换人，也必须走它：从设置里添加第二个账户时，屏幕上正挂着第一个人的对话。

## 6. 服务端改动：只加一条

`POST /v1/auth/logout` —— **撤销调用方自己那枚令牌**（从 `record["tokens"]` 里摘掉它自己那一条，其余设备的令牌不动，`disabled` 不碰）。

- 为什么必须有它：「移除账户」和「退出这台机器」如果只删本机，那枚令牌在服务端还活着，"退出"就是个假动作。与重置密码必须清令牌是同一条理由。
- 它**不进** `PUBLIC_PATHS`：走正常 Bearer，由 `install_auth` 中间件守（`_PROTECTED_PREFIXES` 含 `/v1/`）。路由契约测试会自动要求它声明身份依赖。
- 幂等分两层说清：**存储层** `revoke()` 对"不存在"和"已摘掉"走同一条路——不抛、不返回在不在（返回值上的任何差别都是一个 oracle）。**HTTP 层**第二次拿同一枚令牌来调会在中间件就被挡成 401，压根到不了端点；这不是泄漏，那枚令牌已经不存在了。
- 限流四本账**一律不动**：logout 需要有效凭据，不是免凭据面，没有可枚举的东西要预算。
- 摘掉当前身份之外某人的令牌：客户端**直接带着那条令牌**调同一个端点即可，不需要"先切过去"（先切会把那个人的数据加载到屏幕上，共用设备上没必要）。
- **实现时顺带修掉的同类缺陷**：`auth_router` 那 9 个端点全是 `async def`，却一个 `await` 都没有，而函数内部在跑 bcrypt（登录 1 次、注册 4 次、重置最多 5 次）和写盘——这正是造成 Cloudflare 524 的那一类（见 [[ai-assistant-event-loop-blocking-and-testclient]]）。全部改成同步 `def`，交给 FastAPI 的线程池。`logout` 自己也是 `def`。

## 7. 信任模型与天花板（必须写下来，不是脚注）

**这台浏览器上，清单里的任何人都不需要密码就能被切成当前身份。** 谁拿到这个浏览器（或读到 `localStorage`），就等于拿到清单上全部 5 个人的有效令牌。这是"不重输密码"的直接代价，用户已知并选择。

由此推出的两条硬要求：
- 「退出这台机器」**必须**撤销服务端那枚（§6），否则删掉一条只是把令牌从明面挪到暗处
- 清单里**永不存密码**，只存服务端签发的令牌；找回答案同理（它们本来就只在服务端是摘要）

不在本轮范围、但被这条决定打开的门：以后若要做"切换要 PIN"，PIN 只能挡同浏览器的人，挡不住读到 localStorage 的人——到时候要如实这么写，别写成"更安全"。

## 8. 测试

1. **迁移**：只有 `accessToken+userId` 的老形状 → 启动后变成清单第一条，且三个老键都不在了。
2. **单一事实来源**：`api.js` 里不得再出现 `localStorage.getItem("accessToken")`；`pref.token` 只能被 `setCurrent/addIdentity` 影响。
3. **切换后不残留**（本份最重要的正向锁）：`resetViewForIdentity()` 必须清 §5 那 7 项，逐项断言；且 `switchTo` 里它被调用**在** `loadWho()` **之前**。
4. **不靠 404 兜**：`restore()` 不得再依赖"服务端会回 404"来清 `state.messages`——钉它在 `!pref.sessionId` 时**主动清空**而不是 `return`。
5. **撤销即失效**：`/v1/auth/logout` 之后旧令牌打 `/v1/auth/me` 得 401；同一个人的**另一枚**令牌仍然 200。
6. **移除即撤销**：`dropIdentity` 必须先成功调用 logout 再删本机条目；logout 失败时**保留**本机条目并给一句实话（不能"看起来删掉了但其实还能用"）。
7. **上限**：第 6 个人进来时顶掉最久未用的那条，且被顶那条走同一条 logout。
8. **双击**：切换在途时清单不可再点（与注册/重置同一套锁）。
9. 首屏 ID 接线清单随 §3/§8 同步；`PUBLIC_PATHS` 仍是 3 条（新增锁）。

## 9. 风险与天花板

- 服务端每人最多 8 枚令牌、FIFO 淘汰（`auth.py:307`）。本机清单 5 条 + 其他设备，理论上能把一条被本机记住的令牌挤掉——表现就是 §4 第 6 步那条「需要重新登录」。这是**正确行为**，不是 bug，界面必须说清而不是静默重登。
- 切走掐断会让那半截回答消失，且**没有提示**（用户选的）。若以后有人报"回答丢了"，先回来看这一条。
- 一屏多身份意味着 `applyRole()` 要在每次切换后重跑，否则 A 是管理员、切到 B 还留着管理员入口。
