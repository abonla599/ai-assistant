# 固定三题密保与两步注册 Implementation Plan（修订版）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把密保从"用户自设一个问题"改成**全站固定的三个问题**，注册分成两步、找回按用户名 → 三答案 → 新密码三步走，并补上原实现缺的三件事：重置成功也计预算、答案可轮换、比对不短路。

**Architecture:** 沿用 `1ff52a78` 已经落地的分层——`core/auth.py` 只讲身份规则，`core/auth_router.py` 是它与 HTTP 之间唯一的一层（状态码 + 按真实 IP 计费），`core/authz.py` 的 `PUBLIC_PATHS` 是免凭据面的唯一清单。问题文本是常量，因此**不再有 `/v1/auth/recovery` 这个端点**：前端自己渲染问题，匿名请求者拿不到任何新信息。

**Tech Stack:** Python 3.12 / FastAPI / Pydantic v2 / bcrypt；原生 ES JS 前端（无构建）；pytest + TestClient。

**Spec:** `docs/superpowers/specs/2026-09-17-credentials-and-self-service-reset-design.md`（Task 4 里同步为 as-built）
**取代:** `docs/superpowers/plans/2026-09-17-security-questions-and-self-service-reset.md` 的 Task 1–3（那三个任务已由 `1ff52a78` 以不同形状落地，本计划只处理差异与缺口）

## Global Constraints

- 口令与密保答案**只存 bcrypt 摘要**（`hash_password` 那套 sha256 预哈希 + bcrypt），明文绝不落盘、不进日志、不进响应体。
- 免凭据端点的失败对外必须**同一句话、同一形状、同一耗时**；能区分内部原因的信息只许活在异常对象里。
- 限流按真实来源计费：`CF-Connecting-IP` 优先，取不到才退回 `request.client.host`；`X-Forwarded-For` 一律不信。
- 当事人自己能改好的输入错误不计费；只有"猜"（口令错、答案错、撞名）计费。
- 撤销类动作不得顺手启用账号：`reset_password` 必须原样保留 `disabled`。
- 新密码形状**先于**答案校验；答案比对**不得短路**。
- 提交信息用中文，写"为什么"。

## 三条 Ruling（我替你定的，可推翻）

| # | 判断 | 理由 | 错了的代价 |
|---|---|---|---|
| R1 | 三个答案**必须全对** | "任一对上即放行"会让第二题（"你的用户名是什么？"）变成敞着的门：攻击者读得到用户名就 100% 答得对 | 若本意是"答对任一即可"，这三题里最弱的一题决定整条路的安全性 |
| R2 | 保留你给的三题原文，但**第二题熵为零** | 照你说的实现；它只是让人多填一格 | 无安全收益；若换成"你母亲的姓氏"这类，整条路的强度上升一档 |
| R3 | 比对不短路：三题都算完再判 | 短路会把"第几题猜对了"漏给攻击者，三题就拆成三次独立预算的盲猜 | 漏这一条，预算等于除以 3 |

## 常量（三处必须一字不差，否则测试与界面各说各话）

```python
RECOVERY_QUESTIONS = ("你小学在哪上？", "你的用户名是什么？", "你的父亲叫什么名字？")
```

---

### Task 1: 存储层改成固定三答案（含不短路比对与答案轮换）

**Files:**
- Modify: `backend/app/core/auth.py`
- Test: `backend/tests/test_auth.py`

**Interfaces:**
- Consumes: `hash_password()`、`_check_password()`、`_answer_key()`、`_DUMMY_PW_HASH`、`RESET_FAIL`（均来自 `1ff52a78`）
- Produces:
  - 常量 `RECOVERY_QUESTIONS: tuple[str, str, str]`、`ANSWER_COUNT = 3`
  - 记录键改为 `answer_hashes: list[str]`（长度 3，顺序与 `RECOVERY_QUESTIONS` 对齐）；**不再有** `security_question`
  - `AuthStore.register(username, password, security_answers: list = None)` —— `security_answers` 必须给满 3 条或一条不给
  - `AuthStore.reset_password(username, answers: list, new_password, new_answers: list = None) -> None`
  - **删除** `AuthStore.recovery_question()`（问题已是常量，端点随之在 Task 2 删除）

- [ ] **Step 1: 写失败测试**

```python
# ---------- 固定三题：全对才过、不短路、可轮换 ----------

ANS = ["新市场小学", "hehai2024", "李建国"]


def test_register_needs_all_three_answers_or_none(store):
    for bad in ([], ["新市场小学"], ANS[:2]):
        with pytest.raises(AuthError) as e:
            store.register(username="甲", password="correct-horse-battery", security_answers=bad)
        assert "三" in str(e.value) or "答案" in str(e.value), f"{bad} 的报错没说到三题：{e.value}"
    principal, _ = store.register(username="甲", password="correct-horse-battery", security_answers=ANS)
    record = json.loads(open(store.path, encoding="utf-8").read())[principal.user_id]
    assert len(record["answer_hashes"]) == 3
    assert "security_question" not in record, "问题已是常量，不该再存进每个人名下"
    for answer in ANS:
        assert answer not in open(store.path, encoding="utf-8").read(), "明文答案绝不能落盘"


def test_reset_requires_every_answer_and_never_short_circuits(store):
    """三题全对才放行，而且错在哪一题不许有可观察差别。

    短路返回等于把"第一题猜对了"漏出去：攻击者于是有三份独立预算，
    每 10 分钟各猜一题，整条路的强度除以三。这里用耗时把这条钉住——
    只错第 1 题与只错第 3 题，都必须付满三次 bcrypt 的代价。
    """
    store.register(username="乙", password="correct-horse-battery", security_answers=ANS)
    for wrong in (0, 1, 2):
        bad = list(ANS)
        bad[wrong] = "错的答案"
        with pytest.raises(AuthError) as e:
            store.reset_password(username="乙", answers=bad, new_password="another-correct-horse")
        assert str(e.value) == RESET_FAIL
    store.reset_password(username="乙", answers=ANS, new_password="another-correct-horse")
    assert store.login("乙", "another-correct-horse")[0].username == "乙"


def test_answer_comparison_pays_three_bcrypts_whatever_the_answer(store, monkeypatch):
    """耗时判据：错答案与"查无此人"都必须跑满三次 bcrypt。

    只错第 1 题就返回的话，这里只会数到 1 次——那正是 R3 要挡的事。
    """
    calls = []
    real = auth_module.bcrypt.checkpw

    def spy(pw, hashed):
        calls.append(1)
        return real(pw, hashed)

    monkeypatch.setattr(auth_module.bcrypt, "checkpw", spy)
    store.register(username="丙", password="correct-horse-battery", security_answers=ANS)
    calls.clear()
    with pytest.raises(AuthError):
        store.reset_password(username="丙", answers=["x1", "x2", "x3"],
                             new_password="another-correct-horse")
    assert len(calls) == 3, f"比对短路了：只跑了 {len(calls)} 次"
    calls.clear()
    with pytest.raises(AuthError):
        store.reset_password(username="查无此人", answers=["x1", "x2", "x3"],
                             new_password="another-correct-horse")
    assert len(calls) == 3, "查无此人也要付满三次代价，否则快慢本身就是一份名单"


def test_answers_can_be_rotated_at_reset(store):
    """答案泄露过就该换得掉——固定问题不等于固定答案。"""
    store.register(username="丁", password="correct-horse-battery", security_answers=ANS)
    store.reset_password(username="丁", answers=ANS, new_password="another-correct-horse",
                         new_answers=["沙北", "hehai2025", "李建国"])
    with pytest.raises(AuthError):
        store.reset_password(username="丁", answers=ANS, new_password="third-correct-horse")
    store.reset_password(username="丁", answers=["沙北", "hehai2025", "李建国"],
                         new_password="third-correct-horse")
    assert store.login("丁", "third-correct-horse")[0].username == "丁"


def test_partial_new_answers_are_rejected_without_touching_anything(store):
    principal, token = store.register(username="戊", password="correct-horse-battery", security_answers=ANS)
    with pytest.raises(AuthError):
        store.reset_password(username="戊", answers=ANS, new_password="another-correct-horse",
                             new_answers=["只有一条"])
    assert store.resolve(token) is not None, "被拒的重置不该已经动过令牌或口令"
    assert store.login("戊", "correct-horse-battery")[0].user_id == principal.user_id


def test_reset_still_revokes_tokens_and_never_un_disables(store):
    principal, token = store.register(username="己", password="correct-horse-battery", security_answers=ANS)
    store.disable_user(principal.user_id)
    with pytest.raises(AuthError):
        store.reset_password(username="己", answers=ANS, new_password="another-correct-horse")
    store.enable_user(principal.user_id)
    store.reset_password(username="己", answers=ANS, new_password="another-correct-horse")
    assert store.resolve(token) is None, "改密的理由之一就是让别的设备掉线"
    assert [u for u in store.list_users() if u["user_id"] == principal.user_id][0]["disabled"] is False
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && python -m pytest tests/test_auth.py -q -k "answer or short_circuits or rotates or revokes"`
Expected: FAIL（`register() got an unexpected keyword argument 'security_answers'`）

- [ ] **Step 3: 实现**

要点（顺序即安全属性）：
1. `_check_answer_shapes(answers)`：必须是长度 3 的列表，逐条走 `_check_answer_shape`；不合格抛 `AuthError("三题答案都要填，且每题至少 N 个字符")`。
2. `register`：`security_answers is None` → 不存 `answer_hashes`（老形状，供测试与历史数据）；给了就必须满 3 条。半套（1 或 2 条）与"给了但少一条"同一句话。
3. `reset_password(username, answers, new_password, new_answers=None)`：
   - 先 `_check_password_shape(new_password)`（R：新密码形状先查，别让"密码太短"变成"答案对了"的 oracle）
   - `new_answers` 给了就必须满 3 条，否则 `AuthError`（非 taken、不计费）
   - 取 record → `stored = (record or {}).get("answer_hashes") or [_DUMMY_PW_HASH] * 3`
   - **不短路**：`ok = all([_check_password(_answer_key(a), h) for a, h in zip(answers + ["", "", ""], stored)])`（写成显式循环更清楚：先算完三个 bool，再 `all`）
   - `if not ok or record is None or not record.get("answer_hashes"): raise AuthError(RESET_FAIL)`
   - `if record.get("disabled"): raise AuthError(RESET_FAIL)`
   - 写 `pw_hash`、可选写 `answer_hashes`、`tokens = []`、刷新 `last_used_at`、`_flush()`
4. 删 `recovery_question()` 与 `NO_RECOVERY`（问题已是常量，那句"这个用户名没有设置密码找回"不再有任何地方要说）—— 同时删掉引用它们的测试。

- [ ] **Step 4: 全量存储层测试 + 提交**

Run: `cd backend && python -m pytest tests/test_auth.py -q`
Expected: PASS（`1ff52a78` 里针对 `security_question`/`recovery_question` 的旧断言一并改掉，不许留 `pass`/恒真）

```bash
git add backend/app/core/auth.py backend/tests/test_auth.py
git commit -m "密保改成全站固定三题：全对才放行、比对不短路、答案可轮换"
```

---

### Task 2: 端点收口（删掉 recovery、补成功预算、不清失败账）

**Files:**
- Modify: `backend/app/core/auth_router.py`、`backend/app/core/authz.py`
- Test: `backend/tests/test_auth_endpoints.py`、`backend/tests/test_route_auth_contract.py`、`backend/tests/conftest.py`

**Interfaces:**
- Consumes: Task 1 的 `RECOVERY_QUESTIONS`、`register(..., security_answers=[3])`、`reset_password(username, answers, new_password, new_answers=None)`
- Produces:
  - `POST /v1/auth/register` 入参 `{username, password, security_answers: [str, str, str]}`
  - `POST /v1/auth/reset` 入参 `{username, answers: [3], new_password, new_answers?: [3]}`
  - **删除** `POST /v1/auth/recovery`；`PUBLIC_PATHS` 回到三条
  - 新账本 `RESET_WINDOW_SECONDS = 86400`、`MAX_RESETS_PER_SOURCE = 3`、`_RESETS`

- [ ] **Step 1: 写失败测试**

关键断言（完整代码由实现者按现有 `_reg` / `_reset` 风格补）：
1. 注册少给一条答案 → 422，且 `_budget_used() == 0`。
2. `POST /v1/auth/recovery` 现在必须是 **401/404 且不在路由表里**：断言 `"/v1/auth/recovery" not in {r.path for r in app.routes}`。
3. 猜对第 1 题、错后两题，与全错，响应体逐字节相同（R3 的 HTTP 侧）。
4. **成功重置也要有代价**：同一 IP 重置成功 3 次后，第 4 次即使答案正确也 429，且 `retry-after` 在。
5. **重置成功不再抹掉失败账**：先猜错 5 次、再猜对 1 次，然后接着猜错 5 次应当 429（旧行为里 `_FAILS.pop` 会让它变成还能继续猜）。
6. `PUBLIC_PATHS == {"/v1/auth/register", "/v1/auth/login", "/v1/auth/reset"}`。
7. `conftest._isolated_throttle` 清三本账（`_FAILS`/`_REGISTERS`/`_RESETS`）。

- [ ] **Step 2: 运行确认失败** → **Step 3: 实现** → **Step 4: 全量 + 提交**

实现要点：`ResetRequest.answers: List[str]`、`new_answers: Optional[List[str]] = None`；`reset` 端点里成功路径改成
`_note_reset(ip)` 而**不再** `_FAILS.pop(ip, None)`；`recovery` 端点与 `RecoveryRequest` 整体删除；
`authz.PUBLIC_PATHS` 同步。

```bash
git add backend/app/core/auth_router.py backend/app/core/authz.py backend/tests
git commit -m "找回端点收口：问题已是常量所以不必再问服务器，成功重置也开始记账"
```

---

### Task 3: 两步注册与三步找回（前端）

**Files:**
- Modify: `backend/app/web/static/{index.html,app.js,api.js,style.css}`
- Test: `backend/tests/test_web_pwa.py`

**Interfaces:**
- Consumes: Task 2 的两个入参形状；`1ff52a78` 已有的 `showAuthView()`、`setUserError()`、`rcStep`/`renderRecovery()`
- Produces: 注册 `regStep` 1→2；找回三步；`API.register(username, password, answers)`、`API.resetPassword(username, answers, newPassword)`；不再有 `API.recovery`

界面要求（人给的原文）：
- 第一步：用户名、密码、**确认密码**，下方「下一步」。点下一步**先发不发都行**——但答案还没填，所以只做本地校验（三项非空 + 两次密码一致），不发请求。
- 第二步：一句说明"这三题将来用于忘记密码时自助找回，答案只存在这台机器上"，然后三个输入框，标签就是 `RECOVERY_QUESTIONS` 那三句原文（前端写死同一份常量，Task 4 的契约测试负责比对两边一致）。
- 忘记密码：用户名 → 三题三答案 + 新密码 + 确认新密码 → 提交 `/v1/auth/reset`；成功后回登录模式并提示"密码已重置，其他设备需要重新登录一次"。
- 撞名（409）继续走 `setUserError()`：话写在用户名那一格下面。
- 第二步的"下一步"要能退回（给一个「上一步」），因为填错答案不该逼人重开。

契约测试要新增/改掉的：
1. `test_register_and_me_wrappers_match_the_backend_contract` —— `API.register` 的参数名与 `RegisterRequest.model_fields` 对齐（现在是三个字段，不是四个）。
2. 新增：前端那份问题常量与后端 `RECOVERY_QUESTIONS` **逐字相同**（读 `app.js` 里的数组字面量与 `auth.py` 的常量比对）。这是唯一能挡住"两边各改一版问题"的锁。
3. 新增：`API.recovery` 与 `/v1/auth/recovery` 在前端**不再出现**。
4. 新增：第一步的「下一步」不发任何请求（断言那函数体里没有 `API.`）。
5. 保留：确认密码不一致时不发请求；`submitAuth` 的在途闸门与 finally 解锁。

```bash
git add backend/app/web/static backend/tests/test_web_pwa.py
git commit -m "注册分两步、找回三步走完：问题固定后不必再问服务器要"
```

---

### Task 4: 文档同步为 as-built、重建 EXE、实机验收

- [ ] spec 更新：`docs/superpowers/specs/2026-09-17-credentials-and-self-service-reset-design.md` 里 §3/§4/§5 改成"固定三题、两个端点、两步注册"，并删掉 `/v1/auth/recovery` 那一段；§6 的已接受风险补两条：
  1. 三题里"你的用户名是什么？"熵为零，靠"必须全对"才不成为短板；真要提强度就换掉它。
  2. 答案是 bcrypt 摘要，但**问题与答案的配对是公开的**（问题写死在代码里），所以答案的实际熵就是那几句话的熵——别用城市名、别用姓氏之外的常见词。
- [ ] `docs/用户手册.md`、`docs/安装部署指南.md`：把"自设一个问题"改成"三题固定答案"，注册两步、找回三步的说法与界面一致。
- [ ] 全量测试 → 停服务 → 重建 → **查产物**（`grep -c "RECOVERY_QUESTIONS" _internal/...` 之类不算，要看 `index.html` 里有 `rcAnswers`/新 id、`app.js` 里没有 `API.recovery`）→ 启动 → 数据 md5 逐项比对。
- [ ] 实机（隔离实例，临时数据目录 + 假上游，不碰真实密钥）：注册两步 → 登出 → 忘记密码三步 → 新口令登录 → 旧令牌解不出来；宽屏与 724px 各截一张图。
- [ ] 运维：`/admin` 删掉 `老李`、`实验1号`、`实验一号`（人确认全是试验账户）。
- [ ] 推 develop，等 `test` 检查绿；公网样式没换就先怀疑 Cloudflare 那份 4 小时缓存（Purge 或改 Browser Cache TTL）。
