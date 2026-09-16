# 用户身份绑定与管理员权限分离（多人分发第 1 步）

日期：2026-09-16
状态：待评审
范围：仅"第 1 步"。后端上云（第 2 步）与模型额度付费方式（第 3 步）不在本文内。

## 1. 目标

让 `ai-assistant` 可以在不泄露彼此数据、不交出管理员权限的前提下，把访问凭据发给朋友。

具体三条验收：

- 每个使用者绑定到一个固定的用户名，身份由服务端签发，客户端不能自报
- 用户之间读不到对方的会话、记忆、附件
- 模型服务配置（`/v1/providers/*`）只有管理员能改，且随时能停用某个人

## 2. 现状：已核实的问题

不是推测，均为当前代码行为。

| # | 问题 | 位置 |
|---|---|---|
| 1 | 单个全局 `ACCESS_TOKEN` 即全部权限，无身份、无角色 | `main.py:124-148` |
| 2 | `ACCESS_TOKEN` 未设置时整个 `/v1/*` 完全开放 | `main.py:129` |
| 3 | `/v1/chat` 把身份写死成 `default_user`，所有人共用一个记忆池 | `main.py:270,311` |
| 4 | `user_id` 由客户端随便填 | `memory_router.py` add/search/decay/list |
| 5 | `DELETE /v1/memory/delete` 收下 `user_id` 却完全不用于校验归属 | `memory_router.py:239-254` |
| 6 | 会话无归属概念，`GET /v1/sessions` 返回全部会话，凭 id 可读任意会话 | `session_store.py`（单条键：`created_at,messages,model,session_id,title`，无 owner） |
| 7 | 附件无归属，任何持口令者可枚举并下载别人上传的 PDF | `uploads/index.json`（无 owner） |
| 8 | 记忆查询不带 `where` 过滤，先全局取 `top_k*2` 再用 Python 挑 → 多人时本人记忆被别人挤掉 | `memory_manager.py:188-205` |
| 9 | **跨用户泄露**：本人无命中时兜底返回他人的记忆 | `memory_manager.py:207-215` |

第 9 条尤其注意：pytest 下走 `FakeMemoryStore`（那边按 `user_id` 正常过滤），因此这条泄露在 CI 中不可见。

## 3. 已确认的设计决定

| 决定 | 取值 | 理由 |
|---|---|---|
| token 产生方式 | 一次性邀请码自助注册 | 摩擦最小，且不开放公网注册 |
| 令牌形态 | 存表的随机哑令牌（可撤销） | 必须能随时踢人；JWT 无状态做不到，反而要另存吊销表 |
| 账号形态 | 用户名 + 一次性邀请码 | WebView 壳拿不到设备指纹，"强绑设备"只能是威慑 |
| 重装后恢复 | 仅管理员 `rotate-token` | 若允许"用户名+原码"自助重绑，码即升格为该账号的重置凭据，任何知道码的人可顶号，固定性反而消失 |
| 管理员来源 | `ACCESS_TOKEN` 作为 bootstrap 凭据，解析为 `{user_id:"default_user", role:"admin"}` | 本机 EXE 与已发出 APK 无需改动即继续可用；且 `default_user` 正是今天记忆所用的 id，历史数据零搬运 |
| 无凭据时行为 | 503 拒绝启动服务，不再默认开放 | 忘配 env 不该等于公网裸奔 |

明确不做：设备指纹绑定、邮箱密码账号体系、限流与配额（第 3 步）。

## 4. 设计

### 4.1 新文件与数据结构

`backend/app/core/auth.py`，数据挂在 `data_root()/data/` 下，沿用现有"临时文件 + `os.replace` 原子替换 + 一把锁"的写法。

`data/users.json`：

```json
{
  "u_4f9a21c7": {
    "user_id": "u_4f9a21c7",
    "username": "张三",
    "username_lc": "张三",
    "token_hash": "sha256:9c1e...",
    "role": "user",
    "disabled": false,
    "invite_code": "HENU-2K7Q",
    "created_at": "2026-09-16T20:30:00",
    "last_used_at": "2026-09-16T20:31:00"
  }
}
```

`data/invites.json`：

```json
{
  "HENU-2K7Q": {
    "code": "HENU-2K7Q",
    "max_uses": 1,
    "used_by": [],
    "created_at": "...",
    "created_by": "default_user",
    "expires_at": null,
    "failed_attempts": 0,
    "locked": false
  }
}
```

约束：

- **只存 token 的 SHA-256，绝不存明文。** token 是 `secrets.token_urlsafe(32)`（256 位随机），故 sha256 足够，不需慢哈希。兑换成功后明文只在该次响应里出现一次。理由：`users.json` 与 `sessions.json` 同目录，而本仓库有过 `.env` 被跟踪致密钥泄露 5 个月的前科。
- `user_id` 用随机值（`u_` + 8 位十六进制），**不由用户名派生**：用户名允许中文，拿它当 id 塞进 `/v1/admin/users/{id}` 这类路径要额外转义，得不偿失。用户名判重靠 `username_lc` 字段做 casefold 比较；`admin`、`default_user` 为保留字，注册直接拒。
- 邀请码字符集排除易混的 `0O1I`，因为要在手机上手输。
- 日志一律不得输出 token 或邀请码明文。

对外暴露：`resolve_token()`、`redeem_invite()`、FastAPI 依赖 `current_principal`、`require_admin`。

### 4.2 端点契约

**公开（无需凭据）**：仅 `POST /v1/auth/register`，入参 `{code, username}`。

- 成功 → 200 `{token, user_id, username}`
- 用户名占用 → 409
- 码已用尽/过期/锁定 → 403（不区分具体原因，避免成为探测邀请码是否存在的信道）
- 同一码连续失败 N 次（默认 5）→ 该码 `locked`

**普通用户（依赖 `current_principal`）**：

- `POST /v1/chat`、`POST /v1/chat/stream`：使用 `principal.user_id`；请求体不接受任何身份字段（写死的 `default_user` 删除）
- `GET /v1/sessions`：只返回 `owner == principal.user_id`
- `GET/PUT/DELETE /v1/sessions/{id}[/messages]`：先校验 owner，**非本人一律 404**（403 等于承认该 id 存在，可被枚举）
- `POST /v1/memory/add|search`、`DELETE /v1/memory/delete`、`PUT /v1/memory/update`：`user_id` 从请求体/查询参数**彻底移除**，一律由 principal 推导；delete/update 在动手前按 owner 过滤
- 记忆列表是路径变更：`GET /v1/memory/list/{user_id}` → `GET /v1/memory/list`（身份不再出现在 URL 里）
- `POST /v1/uploads`、`GET /v1/uploads/{id}/file`、`DELETE /v1/uploads/{id}`：校验 owner
- `POST /v1/feedback`：权重调整归因到 `principal.user_id`
- `GET /v1/models`：需 principal（用于渲染下拉），保持现有 key masking

**仅管理员（依赖 `require_admin`）**：

- `/v1/providers/*` 全部 7 个（含设默认、测连通——这些能改掉模型后端）
- `GET /v1/memory/stats`、`POST /v1/memory/decay`（后台任务用）
- 新增：`POST /v1/admin/invites`（生成码）、`GET /v1/admin/invites`、`DELETE /v1/admin/invites/{code}`、`GET /v1/admin/users`、`POST /v1/admin/users/{id}/disable`、`POST /v1/admin/users/{id}/rotate-token`、`DELETE /v1/admin/users/{id}`
- 线上关闭 `/docs`、`/redoc`、`/openapi.json`（路由表本身即侦察材料）

**同时修掉的既有缺陷**（属于"归属"能力的组成部分，不另立项目）：

- `search_memory` 改为把 `where={"user_id": user_id}` 下推给 chroma，取回即本人结果
- **删除 `memory_manager.py:207-215` 的兜底**。本人无命中就返回空，绝不能返回他人记忆。删前需确认无测试依赖该兜底行为（现有 pytest 走 fake store，不覆盖此路径）

### 4.3 鉴权模式与启动策略

- 请求凭据解析顺序：`Authorization: Bearer <token>` → `x-access-token` → 等于 `ACCESS_TOKEN` 时视为 bootstrap admin
- `AUTH_MODE=disabled`（仅开发/测试）：一律解析为 bootstrap admin；启动时打印醒目告警
- 既无 `ACCESS_TOKEN`、`users.json` 中又无任何 admin → `/v1/*` 返回 503「服务端未配置访问凭据」，**不放行**
- `conftest.py` 设 `AUTH_MODE=disabled`，保持既有测试语义；`ACCESS_TOKEN=""` 的旧含义随之作废

### 4.4 迁移与回滚

迁移（全部为加法，幂等）：

- `sessions.json` 每条补 `owner`，缺失回填 `default_user`
- `uploads/index.json` 每条补 `owner`，同样回填
- 记忆不搬运（`user_id` 已在 metadata，且引导 admin 的 id 即 `default_user`）

机制：放在各 store 的 `_load()` 做读时归一化；仅在确实存在缺失时回填，回填前把原件写成 `*.bak-<时间戳>`，再原子替换。**迁移异常则拒绝启动**，不允许带着半迁移的库对外服务（否则 owner 校验会静默放行）。

回滚：迁移纯加法，旧代码不读新键，故 `git revert` 即可，无需数据回滚。收回已发访问 = 删 `users.json`/`invites.json`，世界退回只有 `ACCESS_TOKEN`。

客户端版本偏差风险为零：界面由服务端下发，旧 APK 始终加载最新页面。

## 5. 测试矩阵

### 5.0 契约守卫（本节最重要一条）

枚举全部已注册 `/v1/*` 路由，断言每条要么声明 `current_principal`、要么 `require_admin`、要么在显式公开白名单（只有 `POST /v1/auth/register`）。用于拦住"以后新加端点忘挂鉴权"——当前这些洞正是这么来的。

### 5.1 身份签发

token 只出现一次且 `users.json` 仅含 sha256；同名重注 409；大小写不敏感判重；保留字被拒；同码二次核销 403；连续失败锁码；错 token 401；`disabled` 用户 401；bootstrap 凭据解析出 `role=admin`；无凭据且无 admin → 503；`AUTH_MODE=disabled` → 放行。

### 5.2 跨用户隔离（核心）

- A 上传 PDF，B 取该 id → 404
- B 检索不到 A 的记忆
- B 用 A 的 memory_id 删除 → 删除数 0 且 A 记忆仍在
- A 的会话列表不含 B；A 直接 GET B 的 session_id → 404
- `PUT messages` 改不动他人会话
- A 发消息后，断言 chroma 中该条记忆的 `user_id` 属于 A（专防 `default_user` 回归）
- **多用户下 A 检索自己的记忆必须命中**：先给 B 灌入语义更接近的干扰记忆，再断言 A 仍能取到自己的（覆盖 4.2 的 where 下推与兜底删除）

上述两条无法走 `FakeMemoryStore`——假存储本来就按 `user_id` 过滤，天然通过，验不到真代码。因此必须另加一组**直接对 `MemoryManager` + 临时 chroma 目录**的测试（绕过 `use_fake_store`），否则"where 下推"和"删掉跨用户兜底"这两处改动等于没测。这也是现状第 9 条泄露能一直存活的原因。

### 5.3 管理员边界

普通用户打 7 个 providers 端点全 403；`memory/stats`、`memory/decay` 403；admin 可发码/列用户/停用/rotate-token；**被停用者的旧 token 立刻失效**。

### 5.4 迁移

喂无 owner 的 sessions/uploads → 加载后回填 `default_user` 且留下 `.bak`；重复加载不重复写（幂等）；旧客户端仍多传 `user_id` 字段时被忽略且不 500。

## 6. 影响面

改动文件：`backend/app/core/auth.py`（新）、`backend/app/main.py`、`backend/app/memory/memory_router.py`、`backend/app/memory/memory_manager.py`、`backend/app/session/session_store.py`、`backend/app/core/uploads.py`、`backend/app/web/static/api.js`、`backend/app/web/static/app.js`、`backend/app/web/static/index.html`（首启输码界面）、`backend/tests/conftest.py`、新增 `backend/tests/test_auth_tokens.py` / `test_isolation.py`。

APK 壳不需要改动。

## 7. 遗留到后续步骤

- 第 2 步：后端常驻托管，`ai.fenever.xyz` 直连，弃用隧道与开机自启
- 第 3 步：provider 归属（per-user BYOK）与配额/限流。本设计的 `principal.role` 已留缝，届时不改鉴权结构
- 邀请码分配 UI 目前只走管理端点，不做界面
