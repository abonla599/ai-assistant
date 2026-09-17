# 密保注册与自助重置口令 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让注册带上必填的密保问题与确认密码，并新增一条免凭据的自助重置口令通路（重置成功即吊销该用户全部既有会话令牌）。

**Architecture:** 三层各自收口——`core/auth.py` 只管身份规则（可离线测，不 import FastAPI）；`core/auth_router.py` 是它与 HTTP 之间唯一的一层，负责把内部原因翻译成状态码并按真实 IP 计费；`core/authz.py` 的 `PUBLIC_PATHS` 是免凭据面的唯一清单，由路由契约测试钉住。前端首屏那层从两个模式（登录/注册）扩到三个（+重置），并删掉设置页里重复的第二个注册入口。

**Tech Stack:** Python 3.12 / FastAPI / Pydantic v2 / bcrypt（口令与密保答案共用同一套预哈希+慢哈希）；原生 ES JS 前端（无构建步骤）；pytest + FastAPI TestClient。

**Spec:** `docs/superpowers/specs/2026-09-17-credentials-and-self-service-reset-design.md`

## Global Constraints

- 口令与密保答案**只存摘要**，明文绝不落盘、不进日志、不进响应体（摘要带算法前缀 `bcrypt:`）。
- 免凭据端点的失败对外必须**同一句话、同一形状、同一耗时**；能区分内部原因的信息只许活在异常对象里。
- 限流按真实来源计费：`CF-Connecting-IP` 优先，取不到才退回 `request.client.host`；**`X-Forwarded-For` 一律不信**。
- 当事人自己能改好的输入错误（空、太短、超长、保留字、半对密保）**不计费**；只有"猜"（口令错、答案错、撞名）计费。
- 撤销类动作**不得顺手启用账号**：`rotate_token` 已有的这条不变量，`reset_password` 必须同样遵守。
- 前端不调任何外部服务；界面由服务端下发，旧 APK 自动加载最新页面。
- 测试必须能在 `AUTH_MODE=disabled`（全局 client）与 `enforced`（`enforced` fixture）两种模式下各测到自己那一半。
- 提交信息用中文，写清"为什么"，不写"做了什么"的流水账。

## 计划阶段发现的一处 spec 缺口

spec §4.2 只写了首屏注册表单，但仓库里**有第二个注册入口**：设置页的 `#registerRow` +
`registerFromSettings` + `API.register(username, password)`（`app/web/static/index.html:216-220`、
`app.js:1153-1175`）。`register` 的入参从 2 个变 4 个时，这条入口会静默变成 422。
处理方式：**删掉它**（首屏那层已经覆盖同一个动作，多一条路就多一处能填错、也就要多测一遍的地方），
而不是给它补两格。见 Task 1 Step 7。

## File Structure

| 文件 | 本计划里的责任 |
|---|---|
| `backend/app/core/auth.py` | 密保字段的校验/摘要/比较；`register()` 扩到 4 参；新增 `reset_password()`；`AuthError` 加 `billable` |
| `backend/app/core/auth_router.py` | `RegisterRequest` 新字段；`ResetRequest` + `POST /v1/auth/reset-password`；`_RESETS` 预算；`_register_error`/`_reset_error` 的计费口径 |
| `backend/app/core/authz.py` | `PUBLIC_PATHS` 加一条 |
| `backend/app/web/static/api.js` | `register` 4 参；新增 `resetPassword` |
| `backend/app/web/static/app.js` | 三模式状态机、确认密码、重名红边、删 `registerFromSettings` |
| `backend/app/web/static/index.html` | 密保两格 + 确认密码格；删说明卡；删设置页注册行 |
| `backend/app/web/static/style.css` | 单列居中；`.auth-field.bad`；删 `.auth-side*` |
| `backend/tests/test_auth.py` | 存储层密保与重置契约 |
| `backend/tests/test_auth_endpoints.py` | 注册/重置端点契约 |
| `backend/tests/test_route_auth_contract.py` | 免凭据面清单 |
| `backend/tests/test_web_pwa.py` | 前后端字段契约、界面契约 |
| `backend/tests/conftest.py` | 新账本 `_RESETS` 也要清 |
| `docs/用户手册.md`、`docs/安装部署指南.md` | 密保与自助重置的说明 + 已接受风险 |

---

### Task 1: 注册带密保（存储层 → 端点 → 前端，端到端一条）

**Files:**
- Modify: `backend/app/core/auth.py`（`AuthError`、新增 `_normalize_question`/`_normalize_answer`、`register()`）
- Modify: `backend/app/core/auth_router.py`（`RegisterRequest`、`_register_error`）
- Modify: `backend/app/web/static/api.js:126-127`
- Modify: `backend/app/web/static/app.js`（`submitAuth`、删 `registerFromSettings` 及其绑定）
- Modify: `backend/app/web/static/index.html`（密保两格；删 `#registerRow`）
- Test: `backend/tests/test_auth.py`、`backend/tests/test_auth_endpoints.py`、`backend/tests/test_web_pwa.py`

**Interfaces:**
- Consumes: 现有 `hash_password()`、`_check_password()`、`_normalize_username()`、`_check_password_shape()`
- Produces:
  - `AuthError(reason: str, *, billable: bool = False)`，属性 `.billable`
  - `AuthStore.register(username: str, password: str, security_question: str, security_answer: str) -> tuple[Principal, str]`
  - 记录新增键：`sq_q: str`（明文问题）、`sq_hash: str`（`"bcrypt:" + ...`）
  - 常量 `SQ_QUESTION_MAX = 100`、`SQ_ANSWER_MIN = 2`、`SQ_ANSWER_MAX = 100`
  - HTTP：`POST /v1/auth/register` 入参 `{username, password, security_question, security_answer}`

- [ ] **Step 1: 写存储层失败测试（密保必填、只存摘要、归一化生效）**

在 `backend/tests/test_auth.py` 末尾追加：

```python
# ---------- 密保问题：注册必填，答案只存摘要 ----------

GOOD_SQ = {"security_question": "我小学的班主任姓什么", "security_answer": "  李老师  "}


def test_registration_requires_a_security_question_and_answer(store):
    """密保必填，而且不合格时要说清是哪一格。

    把"密保问题不能为空"推给"用户名不合法"，用户会去改本来正确的那一格。
    """
    for missing, word in (("security_question", "密保问题"), ("security_answer", "密保答案")):
        kwargs = dict(username="甲", password="correct-horse-battery", **GOOD_SQ)
        kwargs[missing] = ""
        with pytest.raises(AuthError) as e:
            store.register(**kwargs)
        assert word in str(e.value), f"{missing} 不合格却没指到那一格：{e.value}"


def test_the_security_answer_is_stored_only_as_a_bcrypt_digest(store):
    principal, _ = store.register(username="乙", password="correct-horse-battery", **GOOD_SQ)
    raw = open(store.path, encoding="utf-8").read()
    record = json.loads(raw)[principal.user_id]
    assert "李老师" not in raw, "明文答案绝不能落盘"
    assert record["sq_q"] == "我小学的班主任姓什么", "问题必须明文存：重置表单要把它显示出来"
    assert record["sq_hash"].startswith("bcrypt:")
```

> 归一化（strip + casefold）与"长答案撞 bcrypt 72 字节墙"这两条**不在这里测**：它们要靠
> `reset_password` 才观察得到，而那是 Task 2 才有的东西。放在这儿只会以
> `AttributeError` 失败，测不到本该测的那件事。两条都在 Task 2 Step 1。

- [ ] **Step 2: 运行，确认失败**

Run: `cd backend && python -m pytest tests/test_auth.py -q -k "security"`
Expected: FAIL —`register() got an unexpected keyword argument 'security_question'`

- [ ] **Step 3: 实现 `AuthError.billable` 与密保校验/摘要**

`backend/app/core/auth.py`：给常量区补三行，并改 `AuthError`。

```python
SQ_QUESTION_MAX = 100
SQ_ANSWER_MIN = 2
SQ_ANSWER_MAX = 100
```

```python
class AuthError(Exception):
    """注册/登录/重置失败。

    reason 是面向用户的那句话。billable 说给 HTTP 层听：True 表示这是一次"猜"
    （口令错、密保答案错、撞名），该按真实 IP 扣限流预算；False 表示当事人自己
    能改好的输入错误（空、太短、保留字），扣它只会把唯一的入口锁死在真人身上。
    用标记而不是让路由去嗅 reason 的字面量：字符串一改，计费就静默失效，
    而那正是"枚举用户名重新变成免费"的形状。
    """

    def __init__(self, reason: str, *, billable: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.billable = billable
```

在 `AuthStore` 里 `_check_password_shape` 之后加两个静态方法：

```python
    @staticmethod
    def _normalize_question(question: str) -> str:
        q = (question or "").strip()
        if not q:
            raise AuthError("密保问题不能为空")
        if len(q) > SQ_QUESTION_MAX:
            raise AuthError(f"密保问题最长 {SQ_QUESTION_MAX} 个字符")
        if re.search(r"[\x00-\x1f\x7f]", q):
            raise AuthError("密保问题含不可见字符")
        return q

    @staticmethod
    def _normalize_answer(answer: str) -> str:
        a = (answer or "").strip()
        if len(a) < SQ_ANSWER_MIN:
            raise AuthError(f"密保答案至少 {SQ_ANSWER_MIN} 个字符")
        if len(a) > SQ_ANSWER_MAX:
            raise AuthError(f"密保答案最长 {SQ_ANSWER_MAX} 个字符")
        return a.casefold()
```

- [ ] **Step 4: 让 `register()` 收下密保，并把撞名标成可计费**

```python
    def register(self, username: str, password: str,
                 security_question: str, security_answer: str):
        """用户名 + 自设密码 + 密保问题，换一个可撤销的会话令牌。注册即登录。

        密保是**必填**：可选就等于没人填，"忘记密码"那条自助路会因此名存实亡。
        代价写在 spec §6——答案的熵通常远低于口令，它同时是一把钥匙和一个门。
        """
        with self._lock:
            cleaned = self._normalize_username(username)
            pw = self._check_password_shape(password)
            question = self._normalize_question(security_question)
            answer = self._normalize_answer(security_answer)
            lc = cleaned.casefold()
            if any(u.get("username_lc") == lc for u in self._users.values()):
                raise AuthError("该用户名已被占用", billable=True)

            user_id = self._new_user_id()
            record = {
                "user_id": user_id,
                "username": cleaned,
                "username_lc": lc,
                "pw_hash": hash_password(pw),
                "sq_q": question,
                "sq_hash": hash_password(answer),
                "tokens": [],
                "role": "user",
                "disabled": False,
                "created_at": _now(),
                "last_used_at": _now(),
            }
            self._users[user_id] = record
            token = self._issue_token(record)
            self._flush()
        return Principal(user_id=user_id, username=cleaned, role="user"), token
```

- [ ] **Step 5: 运行存储层测试**

Run: `cd backend && python -m pytest tests/test_auth.py -q`
Expected: 新增几条 PASS；**原有 `test_register_returns_token_once_and_stores_only_hash` 等会因缺参而 FAIL** —
把 `tests/test_auth.py` 里所有 `store.register(code=..., username=...)` / `store.register(username=..., password=...)`
调用统一补上 `security_question="问题", security_answer="答案"`（用 `GOOD_SQ` 常量复用），
以及 `tests/conftest.py` 的 `enforced` fixture 里 `as_user` 那处注册调用同样补两个参数。

Run: `cd backend && python -m pytest tests/test_auth.py tests/test_auth_endpoints.py -q`
Expected: 全部 PASS（除下一步要改的端点断言）

- [ ] **Step 6: HTTP 层收下四个字段，计费口径改成看 `billable`**

`backend/app/core/auth_router.py`：

```python
class RegisterRequest(BaseModel):
    username: str
    password: str
    security_question: str
    security_answer: str
```

```python
    try:
        principal, token = _store().register(
            username=req.username, password=req.password,
            security_question=req.security_question, security_answer=req.security_answer)
    except AuthError as e:
        raise _register_error(e, ip)
```

```python
def _register_error(e: AuthError, ip: str) -> HTTPException:
    """把存储层的失败原因翻译成状态码，并给唯一那条可被滥用的信道计费。

    看的是 e.billable 这个标记，不是 reason 里的字面量：以前靠嗅"占用"二字，
    谁改一版文案（"已被占用"→"已存在"）计费就静默失效，而枚举用户名重新变成免费。
    """
    if e.billable:
        _note_failure(ip)
        return HTTPException(status_code=409, detail=e.reason)
    return HTTPException(status_code=422, detail=e.reason)
```

`backend/tests/test_auth_endpoints.py` 的 `_reg` 是全文件唯一的注册出口（`_register` 与几十条用例都走它），
**必须在这一步就带上密保**，否则那文件里每一条注册断言当场变 422：

```python
SQ = {"security_question": "我小学的班主任姓什么", "security_answer": "李老师"}


def _reg(username, password=PW, ip=HOME):
    return client.post("/v1/auth/register",
                       json={"username": username, "password": password, **SQ},
                       headers={"CF-Connecting-IP": ip})
```

Run: `cd backend && python -m pytest tests/test_auth_endpoints.py -q`
Expected: PASS —— 撞名计费那几条靠的是 `billable` 标记，不再是嗅 `reason` 里的"占用"二字

- [ ] **Step 7: 前端：注册多两格，并删掉设置页那个重复入口**

`backend/app/web/static/api.js`：

```javascript
    register: (username, password, securityQuestion, securityAnswer) =>
      request("/v1/auth/register", { method: "POST", body: { username, password,
        security_question: securityQuestion, security_answer: securityAnswer } }),
```

`backend/app/web/static/index.html`：在密码那一格之后、`auth-note` 之前插入两格（只在注册模式出现）：

```html
          <div class="auth-field" id="fieldQuestion" hidden>
            <input id="authQuestion" type="text" placeholder="密保问题（自己看得懂就行）"
                   autocomplete="off" maxlength="100">
          </div>
          <div class="auth-field" id="fieldAnswer" hidden>
            <input id="authAnswer" type="text" placeholder="密保答案（忘了口令靠它）"
                   autocomplete="off" maxlength="100">
          </div>
```

`backend/app/web/static/app.js`：`setAuthMode` 里补可见性与取值，`submitAuth` 按模式发不同参数：

```javascript
function setAuthMode(mode) {
  authMode = mode === "register" ? "register" : "login";
  const reg = authMode === "register";
  $("authSwitch").textContent = reg ? "已有账号？去登录" : "立即注册";
  $("authGo").textContent = reg ? "注册并登录" : "登录";
  $("authPass").autocomplete = reg ? "new-password" : "current-password";
  $("authPass").placeholder = reg ? "请设置密码，至少 8 位" : "请输入密码";
  // 密保两格只在注册时出现，也必须真的 disabled 掉：hidden 只是看不见，
  // 不禁用的话浏览器仍会把它们的值算进 form 提交，登录那枪就会带上密保。
  $("fieldQuestion").hidden = !reg;
  $("fieldAnswer").hidden = !reg;
  $("authQuestion").disabled = !reg;
  $("authAnswer").disabled = !reg;
  $("authSub").textContent = reg
    ? "用户名自己定，密码至少 8 位；再设一个只有你懂的问题，忘了口令能自己找回。"
    : "登录后继续；还没有账号就点下面的「立即注册」。";
}
```

```javascript
    const res = authMode === "register"
      ? await API.register(username, password, $("authQuestion").value.trim(), $("authAnswer").value)
      : await API.login(username, password);
```

删除 `registerFromSettings`（`app.js:1153-1175`）、它的绑定 `$("registerBtn").onclick = ...`（`app.js:1293`），
以及 `index.html` 的 `#registerRow` 整块（`index.html:216-220`）。`#registerHint` 保留——`afterAuth` 往里写"已登录为 X"。

- [ ] **Step 8: 更新被这些改动打到的前端契约测试**

`backend/tests/test_web_pwa.py`：
- `test_registration_ui_elements_wired` 的 id 清单加上 `authQuestion`、`authAnswer`，去掉 `regUsername`、`regPass`、`registerBtn`；
- `test_register_and_me_wrappers_match_the_backend_contract` 里解析 `register` 封装参数的正则，期望值改成
  `["username", "password", "securityQuestion", "securityAnswer"]`，并与 `RegisterRequest.model_fields` 逐一对齐；
- 删掉 `test_registration_locks_its_button_while_the_request_is_in_flight` 中 `registerFromSettings` 那一支（入口没了），
  只留 `submitAuth`。

Run: `cd backend && python -m pytest tests/test_web_pwa.py -q`
Expected: PASS

- [ ] **Step 9: 全量测试 + 提交**

Run: `cd backend && python -m pytest -q`
Expected: 全部 PASS（skip 数不变）

```bash
git add backend/app/core/auth.py backend/app/core/auth_router.py backend/app/web/static \
        backend/tests/test_auth.py backend/tests/test_auth_endpoints.py \
        backend/tests/test_web_pwa.py backend/tests/conftest.py
git commit -m "注册加上必填的密保问题与答案：忘记密码从此有了一条自助可走的路"
```

---

### Task 2: 自助重置端点（存储层 `reset_password` + HTTP + 公开面 + 预算）

**Files:**
- Modify: `backend/app/core/auth.py`（新增 `reset_password()`、`_DUMMY_SQ_HASH`、`RESET_FAIL`）
- Modify: `backend/app/core/auth_router.py`（`ResetRequest`、`POST /v1/auth/reset-password`、`_RESETS`）
- Modify: `backend/app/core/authz.py:21`（`PUBLIC_PATHS`）
- Test: `backend/tests/test_auth.py`、`backend/tests/test_auth_endpoints.py`、`backend/tests/test_route_auth_contract.py`、`backend/tests/conftest.py`

**Interfaces:**
- Consumes: Task 1 的 `hash_password()`/`_check_password()`/`_normalize_answer()`/`_normalize_question()`/`AuthError.billable`
- Produces:
  - `AuthStore.reset_password(username, answer, new_password, new_question=None, new_answer=None) -> None`
  - 常量 `RESET_FAIL = "无法重置：请核对用户名与密保答案；仍未成功请联系管理员"`
  - `RESET_WINDOW_SECONDS = 86400`、`MAX_RESETS_PER_SOURCE = 3`、`_RESETS`
  - HTTP：`POST /v1/auth/reset-password` → 成功 `200 {"status": "reset"}`，**不发令牌**

- [ ] **Step 1: 写存储层测试（成功吊销全部令牌、绝不顺手启用、三种失败同一句话）**

追加到 `backend/tests/test_auth.py`：

```python
# ---------- 自助重置：猜不动、且真的撤销 ----------

def _reset(store, username, answer, new="another-correct-horse", **kw):
    return store.reset_password(username=username, answer=answer, new_password=new, **kw)


def test_the_security_answer_is_matched_case_and_space_insensitive(store):
    """归一化只有 strip + casefold：再多做一点（剥标点、繁简转换）会让人
    "明明填对了却被拒"，而那正是他去找管理员、密保形同虚设的时刻。"""
    store.register(username="丙", password="correct-horse-battery", **GOOD_SQ)
    _reset(store, "丙", "  李老师\t")
    assert store.login("丙", "another-correct-horse")[0].username == "丙"


def test_a_long_security_answer_survives_the_bcrypt_72_byte_wall(store):
    """答案和口令是同一类东西：人会往里贴一长句。bcrypt 在 72 字节处静默截断，
    所以必须走 hash_password 那套预哈希，否则两个不同的长答案会撞成同一个摘要。"""
    store.register(username="丁", password="correct-horse-battery",
                   security_question="问题", security_answer="答" * 40)
    with pytest.raises(AuthError):
        _reset(store, "丁", "答" * 39 + "案")
    _reset(store, "丁", "答" * 40)
    assert store.login("丁", "another-correct-horse")[0].username == "丁"


def test_reset_needs_the_right_answer_and_then_changes_the_password(store):
    store.register(username="戊", password="correct-horse-battery", **GOOD_SQ)
    with pytest.raises(AuthError):
        _reset(store, "戊", "王老师")
    with pytest.raises(AuthError):
        store.login("戊", "another-correct-horse")      # 失败的那次不许已经改掉了口令
    _reset(store, "戊", "李老师")
    assert store.login("戊", "another-correct-horse")[0].username == "戊"


def test_reset_revokes_every_existing_session(store):
    """会点"忘记密码"的人，多半正是怀疑账号被别人用着：留着旧会话等于把门开着。"""
    principal, first = store.register(username="己", password="correct-horse-battery", **GOOD_SQ)
    second = store.login("己", "correct-horse-battery")[1]
    _reset(store, "己", "李老师")
    assert store.resolve(first) is None, "重置之前发出的令牌必须一律解不出来"
    assert store.resolve(second) is None
    assert store.login("己", "another-correct-horse")[0].user_id == principal.user_id


def test_reset_never_un_disables_the_account(store):
    """撤销类动作不得顺手启用账号——这条不变量在 rotate_token 上踩过一次。"""
    principal, _ = store.register(username="庚", password="correct-horse-battery", **GOOD_SQ)
    store.disable_user(principal.user_id)
    _reset(store, "庚", "李老师")
    record = [u for u in store.list_users() if u["user_id"] == principal.user_id][0]
    assert record["disabled"] is True, "重置口令不是重新启用账号"


def test_the_three_reset_failures_are_one_sentence(store):
    """查无此人 / 没设过密保 / 答案错，对外必须是同一句话。

    否则这个免凭据端点就成了"哪些账号设过密保"的探测器，而那份名单在开放注册
    之后就是"可以拿哪些人练猜答案"。第三种用手工抹掉 sq_hash 来造——真实库里
    那三个老账户正是这个形状，而它们会被删掉，所以这条只能在测试里活。
    """
    store.register(username="辛", password="correct-horse-battery", **GOOD_SQ)
    store.register(username="没密保", password="correct-horse-battery", **GOOD_SQ)
    no_sq = [u for u in store.list_users() if u["username"] == "没密保"][0]
    store._users[no_sq["user_id"]].pop("sq_hash")

    # 第三支要用那句**真能匹配上假摘要**的答案：否则"没设过密保"会先被答案错挡掉，
    # 测的就还是答案错那一支，那条守卫等于没测。
    cases = (("查无此人", "李老师"), ("辛", "王老师"), ("没密保", "timing-equalizer-answer"))
    reasons = []
    for username, answer in cases:
        with pytest.raises(AuthError) as e:
            _reset(store, username, answer)
        assert e.value.billable is True, f"{username} 这支没标成可计费，限流会静默失效"
        reasons.append(str(e.value))
    assert set(reasons) == {RESET_FAIL}, reasons


def test_reset_can_optionally_replace_the_security_question(store):
    """换不换密保由用户决定（人拍板）：只给一半要报错，给全就一起换掉。"""
    store.register(username="壬", password="correct-horse-battery", **GOOD_SQ)
    with pytest.raises(AuthError) as half:
        _reset(store, "壬", "李老师", new_answer="新答案")        # 只给答案没给问题
    assert "一起" in str(half.value) and not half.value.billable
    _reset(store, "壬", "李老师", new_question="新问题", new_answer="新答案")
    with pytest.raises(AuthError):
        _reset(store, "壬", "李老师")                            # 旧答案从此无效
    _reset(store, "壬", "新答案")
    assert store.login("壬", "another-correct-horse")[0].username == "壬"
```

- [ ] **Step 2: 运行，确认失败**

Run: `cd backend && python -m pytest tests/test_auth.py -q -k "reset"`
Expected: FAIL —`AuthError: name 'RESET_FAIL' is not defined` / `reset_password` 不存在

- [ ] **Step 3: 实现 `reset_password`**

`backend/app/core/auth.py` 常量区加：

```python
# 三种内部原因（查无此人 / 没设过密保 / 答案错）对外只有这一句。
RESET_FAIL = "无法重置：请核对用户名与密保答案；仍未成功请联系管理员"

# 与 _DUMMY_PW_HASH 同一个理由：没设过密保的账号也要付一次 bcrypt 的代价，
# 否则"这个人没设密保"会从响应快慢里漏出来。
_DUMMY_SQ_HASH = hash_password("timing-equalizer-answer")
```

`AuthStore` 里 `login()` 之后加：

```python
    def reset_password(self, username: str, answer: str, new_password: str,
                       new_question: str = None, new_answer: str = None) -> None:
        """凭"用户名 + 密保答案"改口令，并吊销该用户全部既有会话令牌。

        三条出口必须逐字节同一句话、同一耗时（RESET_FAIL + _DUMMY_SQ_HASH），否则这个
        免凭据端点就是"哪些账号设过密保"的探测器。停用状态原样保留：改口令不是重新启用。
        """
        want = (username or "").strip().casefold()
        with self._lock:
            if bool(new_question) != bool(new_answer):
                raise AuthError("密保问题与答案要一起给")
            record = next((u for u in self._users.values()
                           if u.get("username_lc") == want), None)
            stored = (record or {}).get("sq_hash") or _DUMMY_SQ_HASH
            if not _check_password(self._normalize_answer(answer), stored):
                raise AuthError(RESET_FAIL, billable=True)
            if record is None or not record.get("sq_hash"):
                raise AuthError(RESET_FAIL, billable=True)
            pw = self._check_password_shape(new_password)
            if new_question:
                record["sq_q"] = self._normalize_question(new_question)
                record["sq_hash"] = hash_password(self._normalize_answer(new_answer))
            record["pw_hash"] = hash_password(pw)
            record["tokens"] = []
            self._flush()
```

守卫顺序别改：先 `_check_password` 再判 `record is None`，两支合起来才让"没设过密保"这条
守卫真的可达——反过来的话，那支永远被前一行的"答案不对"挡在前面，测试也就测不到它。

- [ ] **Step 4: 运行存储层测试**

Run: `cd backend && python -m pytest tests/test_auth.py -q`
Expected: PASS

- [ ] **Step 5: 写端点测试（状态码、计费、耗时均衡、预算）**

追加到 `backend/tests/test_auth_endpoints.py`（复用该文件已有的 `_reg`、`_register`、`SQ`、
`_budget_used`、`HOME`；**不要**再定义一个 `_register(client, ...)`——那名字已经被占了，
而且签名不同，会把几十条现有用例带歪）：

```python
# ---------- 自助重置 ----------

NEW_PW = "another-correct-horse"


def _reset(username, answer, ip=HOME, **kw):
    return client.post("/v1/auth/reset-password",
                       json={"username": username, "answer": answer,
                             "new_password": NEW_PW, **kw},
                       headers={"CF-Connecting-IP": ip})


def test_reset_changes_the_password_and_issues_no_token():
    assert _register("重置用").get("token")
    res = _reset("重置用", "李老师")
    assert res.status_code == 200, res.text
    assert res.json() == {"status": "reset"}, "成功不该顺手发一枚令牌"
    assert _login("重置用", NEW_PW).status_code == 200


def test_reset_failures_are_one_sentence_for_every_cause():
    """三种原因对外全等：状态码 + 整个响应体。

    只断状态码不够——detail 里多写一句"这个账号没设密保"就足以让端点变成探测器。
    """
    _register("重置用")
    bodies = [_reset("查无此人", "李老师"), _reset("重置用", "王老师"), _reset("重置用", "王老")]
    outcomes = [(r.status_code, r.json()) for r in bodies]
    assert all(o == outcomes[0] for o in outcomes), outcomes
    assert outcomes[0][0] == 401, "认不出的凭据是 401；403 在本仓专指身份真实但角色不够"
    assert outcomes[0][1]["detail"] == RESET_FAIL


def test_reset_failures_cost_the_throttle_budget():
    from app.core.auth_router import MAX_FAILURES_PER_WINDOW

    _register("重置用")
    for i in range(MAX_FAILURES_PER_WINDOW):
        assert _reset("重置用", f"猜{i}").status_code == 401, f"第 {i} 次应当仍是 401"
    blocked = _reset("重置用", "李老师")
    assert blocked.status_code == 429, "猜密保答案必须有代价"
    assert blocked.headers.get("retry-after")


def test_a_bad_new_password_is_not_reported_as_a_wrong_answer():
    """新口令不合格要照实说：推给"答案不对"会让人反复重填密保，而那是有预算的。"""
    _register("重置用")
    res = _reset("重置用", "李老师", new_password="短")
    assert res.status_code == 422
    assert "密保" not in res.json()["detail"]
    assert _budget_used() == 0, "打字错误不该进限流账本"


def test_reset_budget_counts_successes_too():
    """猜答案比猜口令容易：只罚失败等于允许"慢慢试到成功为止"。

    每个用户换一个 IP：默认那个来源一天只允许 3 次成功注册，共用一个桶的话
    这条测的就不是重置预算，而是注册预算。
    """
    from app.core.auth_router import MAX_RESETS_PER_SOURCE

    for i in range(MAX_RESETS_PER_SOURCE):
        ip = f"203.0.113.{i}"
        assert _reg(f"重置预算{i}", ip=ip).status_code == 200
        assert _reset(f"重置预算{i}", "李老师", ip=ip).status_code == 200
    last = "203.0.113.9"
    assert _reg("最后一个", ip=last).status_code == 200
    assert _reset("最后一个", "李老师", ip=last).status_code == 429


def test_half_a_new_security_question_is_a_422():
    _register("重置用")
    res = _reset("重置用", "李老师", new_answer="新答案")
    assert res.status_code == 422
    assert "一起" in res.json()["detail"]
    assert _budget_used() == 0, "只给一半是打字错误，不是攻击"
```

顶部补 `from app.core.auth import RESET_FAIL`。

- [ ] **Step 6: 实现端点与预算**

`backend/app/core/auth_router.py` 常量区：

```python
# 重置成功的预算：与注册同一个形状（记成功，因为失败本来就另有 _FAILS 收着）。
RESET_WINDOW_SECONDS = 86400
MAX_RESETS_PER_SOURCE = 3
```

`_REGISTERS = defaultdict(list)` 之后加 `_RESETS = defaultdict(list)`，并把 `_prune` 改成
`for ledger in (_FAILS, _REGISTERS, _RESETS):`。再加两个函数（放在 `_note_registration` 之后）：

```python
def _resets_full(ip: str) -> bool:
    moment = _now()
    _prune(moment)
    return (len(_recent(_RESETS, ip, RESET_WINDOW_SECONDS, moment))
            >= MAX_RESETS_PER_SOURCE)


def _note_reset(ip: str) -> None:
    _RESETS[ip].append(_now())
```

模型与端点（放在 `login` 之后）：

```python
class ResetRequest(BaseModel):
    username: str
    answer: str
    new_password: str
    new_question: Optional[str] = None
    new_answer: Optional[str] = None


@router.post("/v1/auth/reset-password")
async def reset_password(req: ResetRequest, request: Request):
    """免凭据自助重置：用户名 + 密保答案换新口令，并吊销全部既有会话令牌。

    成功刻意**不发令牌**：重置完让他回登录页用新口令进去，少一条"重置即拿到会话"
    的凭据发放路径。它也因此必须清 tokens——留着旧会话，等于给正在被怀疑的那个人
    留着攻击者的门。
    """
    ip = _client_ip(request)
    if _throttled(ip):
        raise _too_many(FAILURE_WINDOW_SECONDS)
    if _resets_full(ip):
        raise _too_many(RESET_WINDOW_SECONDS)
    try:
        _store().reset_password(
            username=req.username, answer=req.answer, new_password=req.new_password,
            new_question=req.new_question, new_answer=req.new_answer)
    except AuthError as e:
        if e.billable:
            _note_failure(ip)
            raise HTTPException(status_code=401, detail=e.reason)
        raise HTTPException(status_code=422, detail=e.reason)
    _note_reset(ip)
    _FAILS.pop(ip, None)
    return {"status": "reset"}
```

顶部补 `from typing import Optional`。

- [ ] **Step 7: 公开面与清账**

`backend/app/core/authz.py`：

```python
PUBLIC_PATHS = frozenset({"/v1/auth/register", "/v1/auth/login", "/v1/auth/reset-password"})
```

`backend/tests/test_route_auth_contract.py`：

```python
    assert PUBLIC_PATHS == {"/v1/auth/register", "/v1/auth/login", "/v1/auth/reset-password"}
```

并把该测试的 docstring 从"开放注册之后这里是两条"改成三条，理由不变（没有身份的人本来就得能进来拿身份）。

`backend/tests/conftest.py` 的 `_isolated_throttle`：

```python
    from app.core.auth_router import _FAILS, _REGISTERS, _RESETS
```

以及函数体里对三本账各 `.clear()`，docstring 补一句"第三本账 `_RESETS` 记重置成功数，漏清会让后面的重置断言变 429"。

- [ ] **Step 8: 运行 + 全量 + 提交**

Run: `cd backend && python -m pytest tests/test_auth_endpoints.py tests/test_route_auth_contract.py -q`
Expected: PASS（`test_reset_*` 里若 `RESET_FAIL` 未导入，在测试文件顶部加 `from app.core.auth import RESET_FAIL`）

Run: `cd backend && python -m pytest -q`
Expected: 全部 PASS

```bash
git add backend/app/core/auth.py backend/app/core/auth_router.py backend/app/core/authz.py \
        backend/tests/conftest.py backend/tests/test_auth.py backend/tests/test_auth_endpoints.py \
        backend/tests/test_route_auth_contract.py
git commit -m "自助重置口令：猜不动、成功即吊销全部旧会话"
```

---

### Task 3: 登录层最终形态（三模式 + 确认密码 + 重名红边 + 删说明卡）

**Files:**
- Modify: `backend/app/web/static/index.html`（`#authPass2`；删 `<aside class="auth-side">` 整块）
- Modify: `backend/app/web/static/app.js`（`authMode` 三态、`submitAuth` 分模式、`showForgotHint` → `enterResetMode`、重名红边、`showAuth` 去掉填域名）
- Modify: `backend/app/web/static/api.js`（新增 `resetPassword`）
- Modify: `backend/app/web/static/style.css`（单列居中、`.auth-field.bad`、删 `.auth-side*`/`.auth-grid` 的两列与媒体查询）
- Test: `backend/tests/test_web_pwa.py`

**Interfaces:**
- Consumes: Task 1 的 `API.register(...)` 四参、Task 2 的 `POST /v1/auth/reset-password`
- Produces: `authMode ∈ {"login","register","reset"}`；元素 id `authPass2`；`API.resetPassword(username, answer, newPassword)`

- [ ] **Step 1: 写界面契约测试（先红）**

`backend/tests/test_web_pwa.py`：
- **删掉**整条 `test_forgot_password_says_so_instead_of_calling_a_missing_endpoint`：它钉的是
  「忘记密码只写一句提示、不许发出任何请求」，而这正是本次要换掉的行为。留着它，
  正确的实现反而会红——那是最坏的一种绿。
- `test_registration_ui_elements_wired` 的 id 清单里**去掉 `authSide`**（说明卡没了），
  并去掉该测试末尾那段"卡里不许出现 /admin"的反向断言（连卡一起没了，断言也就无的放矢）。
- 把 `test_auth_layer_keeps_the_two_column_layout_and_no_dead_rules`
  整条替换为下面这条（名字也换，钉的东西反了），并新增确认密码/重置模式两条：

```python
def test_auth_layer_is_one_column_and_the_side_card_is_gone():
    """说明卡整块删除（人拍板），两列随之收成单列居中。

    反向断言是重点：卡上那三行话看着无害，但它承诺的是"我们不放二维码也不放协议链接"
    这件已经没有下文的事，而且它把首屏撑成两列，手机上只会挡表单。
    """
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    for dead in (".auth-side", ".auth-host", ".auth-grid"):
        assert dead not in css, f"{dead} 还留在样式表里"
    assert "auth-side" not in html and "authSide" not in js, "说明卡被顺手加回来了"
    assert re.search(r"\.auth-inner\s*\{[^}]*max-width:\s*420px", css), "首屏没收成单列居中"


def test_confirm_password_is_checked_before_the_request_leaves():
    """两次密码不一致就不该发出请求：省一次 422 往返，更不白烧那份按 IP 的预算。"""
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    body = _function_body(js, "submitAuth")
    assert '$("authPass2")' in body, "确认密码没参与提交"
    assert body.index("authPass2") < body.index("API.register"), "不一致时请求已经发出去了"
    assert "两次密码不一样" in body
    assert "API.resetPassword" in body, "reset 模式提交打的不是重置端点"


def test_a_taken_username_is_pointed_at_the_field_that_is_wrong():
    """撞名要显眼：一行小字 + 红边 + 焦点，因为那是用户自己能改好的事。"""
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    body = _function_body(js, "submitAuth")
    assert "该用户名已存在" in body, "撞名文案还是后端那句"
    assert 'classList.add("bad")' in body and "$(\"authUser\").focus()" in body
```

Run: `cd backend && python -m pytest tests/test_web_pwa.py -q`
Expected: 3 条 FAIL

- [ ] **Step 2: 前端实现——HTML**

`backend/app/web/static/index.html`：在密码格之后插入确认密码格，并删掉整个
`<aside class="auth-side" id="authSide">…</aside>`（连同里面 `#authHost`）：

```html
          <div class="auth-field" id="fieldPass2" hidden>
            <input id="authPass2" type="password" placeholder="请再输入一遍密码"
                   autocomplete="new-password" disabled>
          </div>
```

- [ ] **Step 3: 前端实现——模式机与提交**

`backend/app/web/static/app.js`：`setAuthMode` 改成三态（把 Task 1 的两态版本替换掉）：

```javascript
let authMode = "login";
const AUTH_MODES = ["login", "register", "reset"];

function setAuthMode(mode) {
  authMode = AUTH_MODES.includes(mode) ? mode : "login";
  const reg = authMode === "register";
  const reset = authMode === "reset";
  const needsPass2 = reg || reset;
  $("authSwitch").textContent = reg ? "已有账号？去登录" : "立即注册";
  $("authGo").textContent = reset ? "重设密码" : (reg ? "注册并登录" : "登录");
  $("authPass").autocomplete = reg ? "new-password" : "current-password";
  $("authPass").placeholder = reset ? "新密码，至少 8 位"
    : (reg ? "请设置密码，至少 8 位" : "请输入密码");
  // 看不见不等于不存在：hidden 的格子必须同时 disabled，否则浏览器仍把它算进
  // form 提交，登录那枪就会带上密保或确认密码。密保答案在 reset 模式也要出现——
  // 那一格就是重置的凭据。
  for (const [field, input, on] of [["fieldQuestion", "authQuestion", reg],
                                     ["fieldAnswer", "authAnswer", reg || reset],
                                     ["fieldPass2", "authPass2", needsPass2]]) {
    $(field).hidden = !on;
    $(input).disabled = !on;
  }
  $("authUser").placeholder = reset ? "用户名" : "请输入用户名";
  $("authSub").textContent = reset
    ? "填密保答案设一个新密码。重设成功后，其他设备需要重新登录一次。"
    : (reg ? "用户名自己定，密码至少 8 位；再设一个只有你懂的问题，忘了口令能自己找回。"
           : "登录后继续；还没有账号就点下面的「立即注册」。");
}
```

`submitAuth` 全量替换为：

```javascript
async function submitAuth() {
  if (state.registering) return;
  const hint = $("authHint");
  const fail = (text) => { hint.textContent = text; hint.classList.add("err"); };
  const username = $("authUser").value.trim();
  const password = $("authPass").value;
  hint.classList.remove("err");
  $("authUser").parentElement.classList.remove("bad");
  if (!username || !password) { fail("用户名和密码都要填"); return; }

  let send;
  if (authMode === "register") {
    if (password !== $("authPass2").value) { fail("两次密码不一样"); return; }
    send = API.register(username, password, $("authQuestion").value.trim(),
                        $("authAnswer").value);
  } else if (authMode === "reset") {
    if (password !== $("authPass2").value) { fail("两次密码不一样"); return; }
    send = API.resetPassword(username, $("authAnswer").value, password);
  } else {
    send = API.login(username, password);
  }

  state.registering = true;
  $("authGo").disabled = true;
  hint.textContent = authMode === "register" ? "注册中…"
    : (authMode === "reset" ? "重设中…" : "登录中…");
  try {
    const res = await send;
    if (authMode === "reset") {
      // 重置成功不发令牌（少一条"重置即拿到会话"的路径），所以这里不收这层，
      // 而是切回登录模式让他用新密码进去——顺带把"其他设备要重登"说在前面。
      setAuthMode("login");
      $("authPass").value = "";
      hint.textContent = "密码已重置，用新密码登录；其他设备需要重新登录一次";
      return;
    }
    await afterAuth(res);
  } catch (e) {
    if (e.status === 409) {
      // 后端那句"该用户名已被占用"是存储层的实话，界面上换成指哪儿说哪儿的一句，
      // 并把红边和焦点落在真的那一格：让人去改密码框是白耽误他。
      fail("该用户名已存在，换一个试试");
      $("authUser").parentElement.classList.add("bad");
      $("authUser").focus();
    } else {
      fail((authMode === "register" ? "注册失败：" : authMode === "reset" ? "重设失败：" : "登录失败：")
           + e.message);
    }
  } finally {
    state.registering = false;
    $("authGo").disabled = false;
  }
}
```

`showForgotHint` 换成进 reset 模式（`showAuth` 里那句填 `#authHost` 一并删掉）：

```javascript
/** 忘记密码不是"只解释一句"，而是把这一层切到重置模式：那条路真的走得通。 */
function enterResetMode() {
  setAuthMode("reset");
  $("authModal").classList.remove("hidden");
  $("authHint").textContent = "填密保答案，再设一个新密码";
  $("authHint").classList.remove("err");
}
```

绑定与开关：

```javascript
  $("authForgot").onclick = enterResetMode;
  $("authSwitch").onclick = () => setAuthMode(authMode === "register" ? "login" : "register");
```

同时删掉旧的 `showForgotHint` 整个函数（`app.js` 里它已被 `enterResetMode` 取代），
以及 `showAuth()` 中给 `#authHost` 填域名那一行——它随说明卡一起消失。

- [ ] **Step 4: api.js 与 CSS**

`backend/app/web/static/api.js`：

```javascript
    resetPassword: (username, answer, newPassword) =>
      request("/v1/auth/reset-password", { method: "POST",
        body: { username, answer, new_password: newPassword } }),
```

`backend/app/web/static/style.css`：删掉 `.auth-grid`、`.auth-side`、`.auth-host` 与那条
`@media (max-width: 860px)` 里的首屏分支，`.auth-inner` 收成单列居中，并加红边：

```css
.auth-inner { width: 100%; max-width: 420px; display: flex; flex-direction: column; gap: 12px; }
.auth-field.bad input { border-color: var(--danger); }
```

- [ ] **Step 5: 跑前端契约 + 全量**

Run: `cd backend && python -m pytest tests/test_web_pwa.py -q`
Expected: PASS

Run: `cd backend && python -m pytest -q`
Expected: 全部 PASS（skip 数不变）

- [ ] **Step 6: 提交**

```bash
git add backend/app/web/static backend/tests/test_web_pwa.py
git commit -m "首屏收成单列三模式：确认密码、撞名指到那一格、忘记密码真的能重设"
```

---

### Task 4: 文档、重建 EXE 与实机验收

**Files:**
- Modify: `docs/用户手册.md`（注册与找回密码）
- Modify: `docs/安装部署指南.md`（已接受风险清单补密保）
- Rebuild: `dist/run_backend/`

- [ ] **Step 1: 文档改两处**

`docs/用户手册.md`：注册一节写清"密保问题必填、忘了口令点『忘记密码』自己重设、重设后其他设备要重新登录"。
`docs/安装部署指南.md` 的"已接受风险"里追加两条，措辞照 spec §6：

```markdown
- **密保答案是一把可猜、可社工的钥匙**：任何拿到"用户名 + 一句关于他的常识"的人都能改他的口令。
  唯一的压制手段是按真实 IP 的预算（失败 10 次/10 分钟、成功 3 次/24 小时）。
  受众一旦扩大，应当整块撤下自助重置，换回"管理员重置"这一条唯一可审计的路。
- **同宿舍/同校园 NAT 后共享出口 IP**：一起注册或一起重设口令可能撞 3 次/24 小时的成功预算。
```

- [ ] **Step 2: 全量测试 + 重建 + 验产物**

```bash
cd backend && python -m pytest -q                      # 期望全绿
cd .. && powershell -NoProfile -Command "Stop-Process -Id <当前8000上的pid> -Force"
python -m PyInstaller run_backend.spec --noconfirm
ls dist/run_backend/                                   # 只应有 _internal/ 与 run_backend.exe
grep -c "authPass2" dist/run_backend/_internal/app/web/static/index.html
grep -c "auth-side" dist/run_backend/_internal/app/web/static/style.css   # 期望 0
```

判断构建成功**只看产物**，不看退出码。

- [ ] **Step 3: 启动并核对数据未被触碰**

重建前后各记一次 `data/*.json` 的 md5 与 chroma 条数，逐项比对必须一致
（`data/preference.txt` 允许被后台分析器重写，那是派生文件）。

- [ ] **Step 4: 实机走一遍（隔离实例，不碰真实密钥）**

在源码实例上（`AUTH_MODE=enforced` + 全部落点指向临时目录 + 假上游，端口 8011）用浏览器跑：
注册（含密保）→ 登出（清 localStorage）→ 忘记密码 → 重置 → 新口令登录 → 确认旧令牌解不出来。
宽屏与 724px 窄屏各截一张图。跑完**关掉该实例并删掉临时目录**。

- [ ] **Step 5: 公网复验 + 提交 + 推送**

```bash
git add docs/用户手册.md docs/安装部署指南.md
git commit -m "文档补上密保与自助重置：把可猜、可社工这件事写在明面上"
git push origin develop
```

推完在 GitHub Actions 上确认 `test` 检查为 success；公网还要看
`/app/` 里 `authPass2` 在、`auth-side` 不在。**改版后若样式没换，先怀疑 Cloudflare 那份
4 小时的浏览器缓存**（见 `docs/` 与项目记忆：控制台 Purge，或把 Browser Cache TTL 改成"尊重现有标题"）。

---

## Self-Review

**1. Spec coverage**

| spec 条目 | 落在 |
|---|---|
| §4.1 `sq_q`/`sq_hash` 两键 | Task 1 Step 3-4 |
| §4.2 注册四字段、422 说清哪一格、不计费打字错误 | Task 1 Step 1/3/6 |
| §4.3 重置端点、不发令牌、清空 tokens、不碰 disabled、半对密保 422 | Task 2 Step 1/3/6 |
| §4.3 三种失败同一句话同一耗时 | Task 2 Step 1（`test_the_three_reset_failures_are_one_sentence`）+ Step 5（响应体全等） |
| §3 决定 5 成功也计预算 | Task 2 Step 5/6（`_RESETS`） |
| §5 删说明卡 / 单列 / 三模式 / 确认密码不发请求 / 撞名红边 / 眼睛只翻这一格 | Task 1 Step 7（眼睛沿用现状）+ Task 3 |
| §7 老账户直接删 | Task 4 之外的运维动作，见下方"计划外收尾" |
| §8 测试矩阵 | 各 Task 的测试步骤 |
| §9 影响面 12 个文件 | 全部覆盖 |

**2. Placeholder scan**：无 TBD / "稍后实现" / "同上"。起草时混进去过的三处已就地修掉并说明理由：
一条恒真断言（`all(... for e in [])`）、一行无意义赋值、以及"先写假函数名再补一句其实该用哪个"的
把戏——它们都会让测试看着绿而什么都没测，现在换成真断言与真调用。Task 3 里那个
"忘记密码不许发请求"的旧契约也已明确要求整条删除，否则正确的实现反而变红。

**3. Type consistency**：`register(username, password, security_question, security_answer)` 在
存储层、端点、`api.js`、测试里同名同序；`reset_password(username, answer, new_password,
new_question=None, new_answer=None)` 在存储层与端点里一致；`RESET_FAIL` 由 `auth.py` 导出、
`test_auth_endpoints.py` 以 `from app.core.auth import RESET_FAIL` 消费；`_RESETS` /
`MAX_RESETS_PER_SOURCE` / `RESET_WINDOW_SECONDS` 三者在 auth_router 定义、测试引用同名。

## 计划外收尾（不属于任何 task，需要人做一次）

`/admin` 里删掉 `老李`、`实验1号`、`实验一号` 三个试验账户（spec §7 决定 4）。
它们没有 `sq_hash`，因此永远走不通自助重置——这是刻意的，不做回填。
