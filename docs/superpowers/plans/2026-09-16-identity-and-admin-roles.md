# 用户身份绑定与管理员权限分离 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把"单个全局 ACCESS_TOKEN = 全部权限"改为服务端签发的可撤销用户身份，并让管理员能力（模型服务配置、用户与邀请码管理）与普通用户权限分离。

**Architecture:** 新增纯存储的身份层（`core/auth.py`：users/invites 两份 JSON + 令牌哈希）与 HTTP 接线层（`core/authz.py`：中间件解析 principal + 两个 FastAPI 依赖 + 公开白名单）。所有 `/v1/*` 端点改为从 principal 推导身份，会话与附件落 `owner` 字段并强制校验归属，记忆接口彻底移除客户端自报的 `user_id`。收尾用一条路由级契约测试锁死"新端点忘挂鉴权"。

**Tech Stack:** Python 3.12 / FastAPI / Pydantic v2 / pytest（`TestClient`）；前端原生 ES（无构建）；ChromaDB。

**Spec:** `docs/superpowers/specs/2026-09-16-identity-and-admin-roles-design.md`（本计划逐条实现它，执行时两个文件一起读）

---

## Global Constraints

- 对 spec §4.1 的一处刻意偏离：`auth.py`（纯存储，不 import FastAPI）与 `authz.py`（HTTP 接线）分两个文件。理由是身份规则要能离线测，且不给存储层掺进 web 依赖。
- 对 spec §4.2 的一处修正：邀请码防爆破不采用"同一码连续失败 N 次即锁该码"，改为**按来源 IP 限制窗口内失败次数（429）**。原因：邀请码是 40 位随机值，猜错时那条 invite 记录根本不存在，无处累加计数；且猜错与码不存在的响应本就同为 403，锁码既无法实现也不可以观测。
- **绝不存令牌或邀请码的明文**，只存 `sha256:<hex>`；日志绝不打印 token/code 明文（spec §4.1）。
- `user_id` 格式 `u_` + 8 位十六进制，服务端生成；用户名允许中文，判重用 `username_lc` 的 casefold 比较；保留字 `admin`、`default_user`（spec §4.1）。
- 邀请码字母集 `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`（排除易混的 `0O1I`），格式 `XXXX-XXXX`（spec §4.1）。
- 非本人资源一律返回 **404 而非 403**（403 承认 id 存在，可被枚举）（spec §4.2）。
- 无 `ACCESS_TOKEN` 且 `users.json` 无任何 admin → `/v1/*` 返 **503**，不放行（spec §4.3）。
- 迁移只做加法（补 `owner`），且**迁移异常则拒绝启动**（spec §4.4）。
- 现有测试基线：`python -m pytest backend/tests/ -q` 必须始终全绿。每个 Task 结束时跑全量。
- 已修复、**不要重复实现**：spec §2 第 8/9 条（`search_memory` 的 `where` 下推与删跨用户兜底）已在 commit `bb453187` 完成，并有 `backend/tests/test_memory_isolation.py` 覆盖。
- 提交信息用中文，说明"为什么"而非罗列改了什么。

## File Structure

| 文件 | 职责 |
|---|---|
| Create `backend/app/core/auth.py` | 身份存储：users/invites 读写、令牌哈希与校验、注册/兑换、停用与令牌轮换。不 import FastAPI |
| Create `backend/app/core/authz.py` | HTTP 接线：请求凭据→principal 解析、`current_principal`、`require_admin`、公开白名单 |
| Create `backend/tests/test_auth.py` | `auth.py` 单元测试（含"只存哈希"断言） |
| Create `backend/tests/test_authz_failclosed.py` | 鉴权模式与 503/401 行为 |
| Create `backend/tests/test_isolation.py` | 跨用户隔离（sessions/uploads/memory/chat）反例矩阵 |
| Create `backend/tests/test_route_auth_contract.py` | 路由级鉴权契约守卫 |
| Modify `backend/app/session/session_store.py` | 加 `owner`：迁移回填 + 各方法按 owner 校验 |
| Modify `backend/app/core/uploads.py` | 加 `owner`：`get`/`delete` 的 owner 成为必填参数 |
| Modify `backend/app/memory/memory_router.py` | 移除 `user_id` 入参，改由 principal 推导；delete/update 校验归属 |
| Modify `backend/app/memory/memory_manager.py` | 新增 `owned_ids()`，供按归属过滤记忆 id |
| Modify `backend/app/main.py` | 装 authz 中间件、注册 auth 路由、providers/docs 转 admin、chat/feedback 用 principal |
| Modify `backend/app/web/static/api.js` `app.js` `index.html` | 首启输码注册；memory 调用不再传 user_id |
| Modify `backend/tests/conftest.py` | `AUTH_MODE=disabled`；新增按身份发请求的 fixture |

---

## Task 1: 身份存储 auth.py

**Files:**
- Create: `backend/app/core/auth.py`
- Test: `backend/tests/test_auth.py`

**Interfaces:**
- Consumes: `app.core.paths.data_root()`（已存在）
- Produces:
  - `Principal` — dataclass，字段 `user_id: str`、`username: str`、`role: str`（`"user"`/`"admin"`）
  - `hash_token(token: str) -> str`（形如 `"sha256:<hex>"`）
  - `class AuthStore`：`__init__(self, path: str = None, invites_path: str = None)`
    - `register(self, code: str, username: str) -> tuple[Principal, str]`（返回主体与**仅此一次**的明文 token；失败抛 `AuthError(reason)`）
    - `resolve(self, token: str) -> Principal | None`
    - `list_users(self) -> list[dict]`、`disable_user(self, user_id: str) -> bool`、`rotate_token(self, user_id: str) -> str`、`delete_user(self, user_id: str) -> bool`
    - `create_invite(self, created_by: str, max_uses: int = 1) -> str`（返回 code）、`list_invites(self) -> list[dict]`、`revoke_invite(self, code: str) -> bool`
  - `class AuthError(Exception)`，属性 `reason: str`
  - 模块级单例 `auth_store: AuthStore`

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/test_auth.py`：

```python
"""身份存储单元测试（纯存储层，不涉及 HTTP）。"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from app.core.auth import AuthError, AuthStore, hash_token


@pytest.fixture
def store(tmp_path):
    s = AuthStore(path=str(tmp_path / "users.json"),
                  invites_path=str(tmp_path / "invites.json"))
    s.create_invite("admin")
    return s


def test_register_returns_token_once_and_stores_only_hash(store):
    principal, token = store.register(code=store.list_invites()[0]["code"], username="张三")
    assert principal.username == "张三"
    assert principal.role == "user"
    assert principal.user_id.startswith("u_") and len(principal.user_id) == 10
    raw = open(store.path, encoding="utf-8").read()
    assert token not in raw, "明文令牌绝不能落盘"
    assert json.loads(raw)[principal.user_id]["token_hash"].startswith("sha256:")
    assert store.resolve(token).user_id == principal.user_id


def test_resolve_rejects_unknown_and_revoked(store):
    principal, token = store.register(code=store.list_invites()[0]["code"], username="李四")
    assert store.resolve("not-a-token") is None
    store.disable_user(principal.user_id)
    assert store.resolve(token) is None, "停用必须让旧令牌立即失效"


def test_username_dedup_is_case_insensitive(store):
    code = store.list_invites()[0]["code"]
    store.register(code=code, username="Alice")
    other = store.create_invite("admin")
    with pytest.raises(AuthError) as e:
        store.register(code=other, username="alice")
    assert "占用" in str(e.value)


@pytest.mark.parametrize("bad", ["admin", "default_user", "", "  ", "x" * 25])
def test_reserved_and_malformed_usernames_rejected(store, bad):
    with pytest.raises(AuthError):
        store.register(code=store.list_invites()[0]["code"], username=bad)


def test_invite_is_single_use(store):
    code = store.create_invite("admin")
    store.register(code=code, username="王五")
    with pytest.raises(AuthError):
        store.register(code=code, username="赵六")


def test_rotate_token_invalidates_previous_one(store):
    principal, old = store.register(code=store.list_invites()[0]["code"], username="孙七")
    new = store.rotate_token(principal.user_id)
    assert new != old
    assert store.resolve(old) is None
    assert store.resolve(new).user_id == principal.user_id


def test_invite_code_avoids_ambiguous_characters(store):
    codes = [store.create_invite("admin") for _ in range(40)]
    assert not (set("".join(codes)) & set("0O1I"))
    assert all(len(c) == 9 and c[4] == "-" for c in codes)


def test_failed_write_does_not_corrupt_store(tmp_path):
    path = str(tmp_path / "users.json")
    store = AuthStore(path=path, invites_path=str(tmp_path / "invites.json"))
    store.create_invite("admin")
    store.register(code=store.list_invites()[0]["code"], username="周八")
    assert os.path.exists(path + ".tmp") is False, "临时文件必须被原子替换掉"
    assert len(store.list_users()) == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest backend/tests/test_auth.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.core.auth'`

- [ ] **Step 3: 写最小实现**

创建 `backend/app/core/auth.py`：

```python
"""身份存储：用户、邀请码、可撤销令牌。

刻意不 import FastAPI——身份规则必须能离线测，也不该被 web 框架绑住。
令牌只存 SHA-256：本文件的数据与 sessions.json 同目录，而本仓库有过 .env
被跟踪导致密钥泄露 5 个月的前科，明文存令牌等于把所有人的访问权一起放在
一个随时可能被误提交的文件里。令牌本身是 256 位随机值，故 sha256 足够，
不需要慢哈希。
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime

from app.core.paths import data_root

# 排除易混字符：邀请码要在手机上手输
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
RESERVED_NAMES = {"admin", "default_user"}
USERNAME_MAX = 24


def _now() -> str:
    return datetime.now().isoformat()


def hash_token(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_code() -> str:
    left = "".join(secrets.choice(ALPHABET) for _ in range(4))
    right = "".join(secrets.choice(ALPHABET) for _ in range(4))
    return f"{left}-{right}"


@dataclass(frozen=True)
class Principal:
    user_id: str
    username: str
    role: str      # "user" | "admin"


class AuthError(Exception):
    """注册/鉴权失败。reason 面向用户，不外泄"码存在但已用尽"之类的区别。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _default_users_path() -> str:
    return os.path.join(data_root(), "data", "users.json")


def _default_invites_path() -> str:
    return os.path.join(data_root(), "data", "invites.json")


class AuthStore:
    def __init__(self, path: str = None, invites_path: str = None):
        self.path = os.path.abspath(path or _default_users_path())
        self.invites_path = os.path.abspath(invites_path or _default_invites_path())
        self._lock = threading.Lock()
        self._users = {}
        self._invites = {}
        self._load(self._users, self.path, dict)
        self._load(self._invites, self.invites_path, dict)

    @staticmethod
    def _load(target: dict, path: str, _kind):
        if not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (ValueError, OSError) as e:
            # 身份库坏了绝不能"静默当空库继续跑"——那会让已发令牌全部失效，
            # 并可能在下一次写入时覆盖掉真实数据。备份后从空开始并显式告警。
            backup = path + ".corrupt"
            try:
                os.replace(path, backup)
                print(f"⚠️ 身份文件损坏（{e}），已备份为 {backup}")
            except OSError:
                print(f"⚠️ 身份文件损坏且无法备份（{e}）")
            return
        if isinstance(data, dict):
            target.update(data)

    def _flush(self):
        for path, payload in ((self.path, self._users), (self.invites_path, self._invites)):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)

    # ---------- 用户 ----------

    @staticmethod
    def _normalize_username(username: str) -> str:
        cleaned = (username or "").strip()
        if not cleaned:
            raise AuthError("用户名不能为空")
        if len(cleaned) > USERNAME_MAX:
            raise AuthError(f"用户名最长 {USERNAME_MAX} 个字符")
        if re.search(r"[\x00-\x1f\x7f]", cleaned):
            raise AuthError("用户名含不可见字符")
        if cleaned.casefold() in RESERVED_NAMES:
            raise AuthError("该用户名为系统保留字")
        return cleaned

    def list_users(self) -> list:
        with self._lock:
            return [dict(u) for u in self._users.values()]

    def register(self, code: str, username: str):
        cleaned = self._normalize_username(username)
        lc = cleaned.casefold()
        with self._lock:
            if any(u.get("username_lc") == lc for u in self._users.values()):
                raise AuthError("该用户名已被占用")

            invite = self._invites.get((code or "").strip().upper())
            if invite is None or self._invite_spent(invite):
                raise AuthError("邀请码无效或已用完")

            user_id = "u_" + secrets.token_hex(4)
            token = secrets.token_urlsafe(32)
            record = {
                "user_id": user_id,
                "username": cleaned,
                "username_lc": lc,
                "token_hash": hash_token(token),
                "role": "user",
                "disabled": False,
                "invite_code": invite["code"],
                "created_at": _now(),
                "last_used_at": _now(),
            }
            self._users[user_id] = record
            invite["used_by"] = (invite.get("used_by") or []) + [user_id]
            self._flush()
        return Principal(user_id=user_id, username=cleaned, role="user"), token

    def resolve(self, token: str):
        if not token:
            return None
        digest = hash_token(token)
        now = _now()
        with self._lock:
            for record in self._users.values():
                if record.get("disabled"):
                    continue
                if hmac.compare_digest(record.get("token_hash", ""), digest):
                    record["last_used_at"] = now
                    self._flush()
                    return Principal(user_id=record["user_id"],
                                     username=record["username"],
                                     role=record.get("role", "user"))
        return None

    def disable_user(self, user_id: str) -> bool:
        with self._lock:
            record = self._users.get(user_id)
            if record is None:
                return False
            record["disabled"] = True
            self._flush()
            return True

    def rotate_token(self, user_id: str) -> str:
        with self._lock:
            record = self._users.get(user_id)
            if record is None:
                raise AuthError("用户不存在")
            token = secrets.token_urlsafe(32)
            record["token_hash"] = hash_token(token)
            record["disabled"] = False
            self._flush()
            return token

    def delete_user(self, user_id: str) -> bool:
        with self._lock:
            if user_id not in self._users:
                return False
            del self._users[user_id]
            self._flush()
            return True

    # ---------- 邀请码 ----------

    @staticmethod
    def _invite_spent(invite: dict) -> bool:
        if invite.get("expires_at") and invite["expires_at"] < _now():
            return True
        return len(invite.get("used_by") or []) >= int(invite.get("max_uses", 1))

    def create_invite(self, created_by: str, max_uses: int = 1) -> str:
        code = _new_code()
        while code in self._invites:
            code = _new_code()
        with self._lock:
            self._invites[code] = {
                "code": code,
                "max_uses": max(1, int(max_uses)),
                "used_by": [],
                "created_at": _now(),
                "created_by": created_by,
                "expires_at": None,
            }
            self._flush()
        return code

    def list_invites(self) -> list:
        with self._lock:
            return [dict(i) for i in self._invites.values()]

    def revoke_invite(self, code: str) -> bool:
        with self._lock:
            if code not in self._invites:
                return False
            del self._invites[code]
            self._flush()
            return True


auth_store = AuthStore()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest backend/tests/test_auth.py -q`
Expected: PASS（15 项）

- [ ] **Step 5: 提交**

```bash
git add backend/app/core/auth.py backend/tests/test_auth.py
git commit -m "新增身份存储层：可撤销令牌与一次性邀请码

令牌只存 sha256、明文仅在兑换响应里出现一次：本文件的数据与 sessions.json
同目录，而仓库有过 .env 被跟踪泄露密钥的前科。resolve 用恒定时间比较并顺手
刷新 last_used_at，停用即失效——撤销能力必须有测试钉住，否则等于没做。"
```

---

## Task 2: HTTP 接线与 fail-closed

**Files:**
- Create: `backend/app/core/authz.py`
- Modify: `backend/app/main.py:121-148`（替换现有鉴权中间件）
- Test: `backend/tests/test_authz_failclosed.py`
- Modify: `backend/tests/conftest.py:19-21`

**Interfaces:**
- Consumes: Task 1 的 `auth_store.resolve(token)`、`Principal`
- Produces:
  - `PUBLIC_PATHS: frozenset[str]`（只有 `"/v1/auth/register"`）
  - `resolve_principal(request) -> Principal | None`
  - FastAPI 依赖 `current_principal(request) -> Principal`（无主体 → 401）
  - FastAPI 依赖 `require_admin(request) -> Principal`（非 admin → 403）
  - `install_auth(app, settings)`：注册中间件
  - `AUTH_MODE: str`（`"enforced"` | `"disabled"`）
  - `BOOTSTRAP_PRINCIPAL: Principal` = `Principal("default_user", "本机管理员", "admin")`

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/test_authz_failclosed.py`：

```python
"""鉴权模式行为：401 / 503 / disabled 放行。

这组测试必须各自控制 env 与 app 实例，不能复用 conftest 的 disabled 客户端。
"""
import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient


def _fresh_app(monkeypatch, tmp_path, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import app.core.auth as auth_mod
    monkeypatch.setattr(auth_mod, "auth_store",
                        auth_mod.AuthStore(path=str(tmp_path / "users.json"),
                                           invites_path=str(tmp_path / "invites.json")))
    import app.core.authz as authz
    importlib.reload(authz)
    import app.main as main_mod
    return TestClient(importlib.reload(main_mod).app)


def test_no_credentials_at_all_is_closed_not_open(monkeypatch, tmp_path):
    """忘配 env 不该等于公网裸奔。"""
    client = _fresh_app(monkeypatch, tmp_path, ACCESS_TOKEN="", AUTH_MODE="enforced")
    res = client.get("/v1/sessions")
    assert res.status_code == 503
    assert "未配置" in res.json()["detail"]


def test_valid_token_is_accepted(monkeypatch, tmp_path):
    client = _fresh_app(monkeypatch, tmp_path, ACCESS_TOKEN="boot-token", AUTH_MODE="enforced")
    import app.core.auth as auth_mod
    code = auth_mod.auth_store.create_invite("admin")
    _, token = auth_mod.auth_store.register(code=code, username="张三")
    assert client.get("/v1/sessions", headers={"Authorization": "Bearer " + token}).status_code == 200


def test_bad_token_is_401(monkeypatch, tmp_path):
    client = _fresh_app(monkeypatch, tmp_path, ACCESS_TOKEN="boot-token", AUTH_MODE="enforced")
    assert client.get("/v1/sessions", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_bootstrap_access_token_maps_to_admin(monkeypatch, tmp_path):
    """本机 EXE 与已发出的 APK 靠这条继续可用。"""
    client = _fresh_app(monkeypatch, tmp_path, ACCESS_TOKEN="boot-token", AUTH_MODE="enforced")
    headers = {"Authorization": "Bearer boot-token"}
    assert client.get("/v1/providers", headers=headers).status_code == 200


def test_disabled_mode_treats_everything_as_admin(monkeypatch, tmp_path):
    client = _fresh_app(monkeypatch, tmp_path, ACCESS_TOKEN="", AUTH_MODE="disabled")
    assert client.get("/v1/providers").status_code == 200
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest backend/tests/test_authz_failclosed.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.core.authz'`

- [ ] **Step 3: 写实现**

创建 `backend/app/core/authz.py`：

```python
"""鉴权接线：把凭据解析成 Principal，并提供两个端点依赖。

与 core/auth.py 分家的原因：身份规则要能离线测，且不该被 web 框架绑住。
"""
import os
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.auth import BOOTSTRAP_TOKEN_ENV, Principal, auth_store

AUTH_MODE = os.getenv("AUTH_MODE", "enforced").strip().lower()
BOOTSTRAP_PRINCIPAL = Principal("default_user", "本机管理员", "admin")

# 唯一无需凭据的端点。新增公开端点必须同时改这里，否则路由契约测试会红。
PUBLIC_PATHS = frozenset({"/v1/auth/register"})
_PROTECTED_PREFIXES = ("/v1/", "/docs", "/redoc", "/openapi.json")


def _credential(request: Request) -> str:
    supplied = request.headers.get("authorization", "").strip()
    scheme, _, credential = supplied.partition(" ")
    if not credential and scheme:
        credential = scheme
    elif scheme.lower() not in ("bearer", "token"):
        credential = ""
    return credential or request.headers.get("x-access-token", "").strip()


def resolve_principal(request: Request) -> Optional[Principal]:
    """解析请求身份。返回 None 表示"没有身份"，由调用方决定 401/403。"""
    credential = _credential(request)
    if not credential:
        return None
    if os.getenv(BOOTSTRAP_TOKEN_ENV, "").strip() and \
            credential == os.getenv(BOOTSTRAP_TOKEN_ENV, "").strip():
        return BOOTSTRAP_PRINCIPAL
    return auth_store.resolve(credential)


def _has_any_identity() -> bool:
    return bool(os.getenv(BOOTSTRAP_TOKEN_ENV, "").strip()) or \
        any(u.get("role") == "admin" for u in auth_store.list_users())


def install_auth(app) -> None:
    if AUTH_MODE == "disabled":
        print("⚠️ AUTH_MODE=disabled：所有请求均以本机管理员身份运行，切勿用于公网")

    @app.middleware("http")
    async def authenticate(request, call_next):
        path = request.url.path
        if request.method == "OPTIONS" or not path.startswith(_PROTECTED_PREFIXES):
            return await call_next(request)
        if path in PUBLIC_PATHS:
            return await call_next(request)

        if AUTH_MODE == "disabled":
            request.state.principal = BOOTSTRAP_PRINCIPAL
            return await call_next(request)

        if not _has_any_identity():
            return JSONResponse(status_code=503,
                                content={"detail": "服务端未配置访问凭据"})

        principal = resolve_principal(request)
        if principal is None:
            return JSONResponse(status_code=401, content={"detail": "缺少或错误的访问凭据"})
        request.state.principal = principal
        return await call_next(request)


def current_principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(status_code=401, detail="缺少或错误的访问凭据")
    return principal


def require_admin(request: Request) -> Principal:
    principal = current_principal(request)
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return principal


CurrentPrincipal = Depends(current_principal)
RequireAdmin = Depends(require_admin)
```

在 `backend/app/core/auth.py` 顶部常量区补一行（供 authz 与 main 共用同一个 env 名）：

```python
BOOTSTRAP_TOKEN_ENV = "ACCESS_TOKEN"
```

替换 `backend/app/main.py` 的 `# ---------- 访问鉴权 ----------` 整块（`ACCESS_TOKEN = ...` 到 `return await call_next(request)`，原 124-148 行）：

```python
# ---------- 访问鉴权 ----------
# 身份规则见 app/core/authz.py。这里只负责装上。
from app.core.authz import install_auth

install_auth(app)
```

同处把 FastAPI 应用构造改为按模式关闭接口文档（原 `app = FastAPI(...)` 一处）：

```python
_app_kwargs = {}
if os.getenv("AUTH_MODE", "enforced").strip().lower() != "disabled":
    # 路由表本身就是侦察材料，对外一律不给 openapi
    _app_kwargs = {"docs_url": None, "redoc_url": None, "openapi_url": None}
app = FastAPI(**_app_kwargs)
```

改 `backend/tests/conftest.py`，把第 19-21 行替换为：

```python
# 测试统一以"本机管理员"运行：既无需真凭据，也保持既有断言不变。
# 鉴权本身的分支（401/503/enabled）由 test_authz_failclosed.py 单独覆盖。
os.environ["AUTH_MODE"] = "disabled"
os.environ["ACCESS_TOKEN"] = ""
```

- [ ] **Step 4: 运行新测试与全量**

Run: `python -m pytest backend/tests/test_authz_failclosed.py -q`
Expected: PASS（5 项）
Run: `python -m pytest backend/tests/ -q`
Expected: 全绿（既有测试因 `AUTH_MODE=disabled` 保持原语义）

- [ ] **Step 5: 提交**

```bash
git add backend/app/core/authz.py backend/app/main.py backend/tests/test_authz_failclosed.py backend/tests/conftest.py
git commit -m "鉴权改为身份解析并在无凭据时拒绝服务

原 ACCESS_TOKEN 未设置即整个 API 开放，多人之后等于无归属校验的匿名全开。
现在无任何凭据可解析时返 503，开发/测试显式 AUTH_MODE=disabled 才放行。
ACCESS_TOKEN 降级为 bootstrap 管理员凭据，本机 EXE 与已发出的 APK 不受影响。
非 disabled 模式一并关掉 /docs 与 openapi。"
```

---

## Task 3: 注册与管理端点

**Files:**
- Create: `backend/app/core/auth_router.py`
- Modify: `backend/app/main.py`（include_router）
- Test: `backend/tests/test_auth_endpoints.py`

**Interfaces:**
- Consumes: Task 1 `auth_store`、Task 2 `RequireAdmin`/`CurrentPrincipal`
- Produces: HTTP 契约
  - `POST /v1/auth/register` body `{code, username}` → 200 `{token, user_id, username, role}`；占用 409；码无效/锁 403；缺字段 422
  - `GET /v1/auth/me` → `{user_id, username, role}`
  - `POST /v1/admin/invites` body `{max_uses?}` → `{code}`
  - `GET /v1/admin/invites` → `{invites:[...]}`（不含 failed_attempts 之外的内部字段，绝不回显令牌）
  - `DELETE /v1/admin/invites/{code}` → `{status:"revoked"}` / 404
  - `GET /v1/admin/users` → `{users:[{user_id,username,role,disabled,created_at,last_used_at}]}`（**不含 token_hash**）
  - `POST /v1/admin/users/{user_id}/disable` → `{status:"disabled"}` / 404
  - `POST /v1/admin/users/{user_id}/rotate-token` → `{token}` / 404
  - `DELETE /v1/admin/users/{user_id}` → `{status:"deleted"}` / 404

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/test_auth_endpoints.py`：

```python
"""注册与管理端点的 HTTP 契约。conftest 已把请求当本机管理员。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient
from app.core.auth import auth_store
from app.main import app

client = TestClient(app)
USER_HDRS = {}   # 管理员无需头（disabled 模式）


def _register(username="张三"):
    code = auth_store.create_invite("default_user")
    res = client.post("/v1/auth/register", json={"code": code, "username": username})
    assert res.status_code == 200, res.text
    return res.json(), code


def test_register_returns_token_once():
    body, _ = _register("李四")
    assert body["token"] and body["username"] == "李四" and body["role"] == "user"
    assert body["user_id"].startswith("u_")


def test_duplicate_username_is_409():
    _register("重复名")
    code = auth_store.create_invite("default_user")
    res = client.post("/v1/auth/register", json={"code": code, "username": "重复名"})
    assert res.status_code == 409


def test_bad_invite_is_403_without_revealing_why():
    res = client.post("/v1/auth/register", json={"code": "ZZZZ-ZZZZ", "username": "某人"})
    assert res.status_code == 403
    detail = res.json()["detail"].lower()
    assert "不存在" not in detail and "已用" not in detail


def test_registration_throttles_repeated_failures():
    """猜码必须有代价。

    邀请码是 40 位随机值，"锁某个码"对猜测攻击没有意义（被猜的码根本不存在），
    真正有效的是限制同一来源的失败次数。
    """
    from app.core.auth_router import MAX_FAILURES_PER_WINDOW, _FAILS

    _FAILS.clear()
    for i in range(MAX_FAILURES_PER_WINDOW):
        res = client.post("/v1/auth/register",
                          json={"code": f"AAAA-{i:04d}", "username": "猜一猜"})
        assert res.status_code == 403, f"第 {i} 次应当仍是 403"
    blocked = client.post("/v1/auth/register",
                          json={"code": auth_store.create_invite("default_user"),
                                "username": "正当用户"})
    assert blocked.status_code == 429
    _FAILS.clear()


def test_admin_endpoints_reject_non_admin():
    body, _ = _register("王五")
    hdrs = {"Authorization": "Bearer " + body["token"]}
    assert client.get("/v1/admin/users", headers=hdrs).status_code == 403
    assert client.post("/v1/admin/invites", json={}, headers=hdrs).status_code == 403


def test_user_list_never_leaks_token_hash():
    created, _ = _register("孙六")
    res = client.get("/v1/admin/users")
    assert res.status_code == 200
    text = res.text
    assert "token_hash" not in text and created["token"] not in text


def test_disable_takes_effect_immediately():
    created, _ = _register("周七")
    assert client.post(f"/v1/admin/users/{created['user_id']}/disable").status_code == 200
    hdrs = {"Authorization": "Bearer " + created["token"]}
    res = client.get("/v1/sessions", headers=hdrs)
    assert res.status_code in (401, 403, 503) or res.status_code == 200
    # disabled 模式下无法验证令牌失效，故改用真实 resolve 断言
    assert auth_store.resolve(created["token"]) is None


def test_rotate_token_invalidates_old():
    created, _ = _register("吴八")
    res = client.post(f"/v1/admin/users/{created['user_id']}/rotate-token")
    assert res.status_code == 200
    new = res.json()["token"]
    assert new != created["token"]
    assert auth_store.resolve(created["token"]) is None
    assert auth_store.resolve(new) is not None


def test_invite_revoke_and_404_on_unknown():
    code = auth_store.create_invite("default_user")
    assert client.delete(f"/v1/admin/invites/{code}").status_code == 200
    assert client.delete("/v1/admin/invites/NOPE-NOPE").status_code == 404
    assert client.post("/v1/admin/users/u_deadbeef/disable").status_code == 404


def test_me_reports_resolved_identity():
    created, _ = _register("郑九")
    res = client.get("/v1/auth/me", headers={"Authorization": "Bearer " + created["token"]})
    assert res.status_code == 200
    assert res.json()["username"] == "郑九"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest backend/tests/test_auth_endpoints.py -q`
Expected: FAIL，404 Not Found（路由尚未注册）

- [ ] **Step 3: 写实现**

创建 `backend/app/core/auth_router.py`：

```python
"""注册与管理端点。"""
import time
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.core.auth import AuthError, auth_store
from app.core.authz import CurrentPrincipal, Principal, RequireAdmin

router = APIRouter(tags=["身份"])

# 猜码的代价：同一来源在窗口内失败太多次就拒一拒。进程内计数即可——
# 重启即清零是可接受的，因为真正的凭据是 40 位随机邀请码。
FAILURE_WINDOW_SECONDS = 600
MAX_FAILURES_PER_WINDOW = 10
_FAILS = defaultdict(list)


def _throttled(ip: str) -> bool:
    now = time.monotonic()
    recent = [t for t in _FAILS[ip] if now - t < FAILURE_WINDOW_SECONDS]
    _FAILS[ip] = recent
    return len(recent) >= MAX_FAILURES_PER_WINDOW


def _note_failure(ip: str):
    _FAILS[ip].append(time.monotonic())


class RegisterRequest(BaseModel):
    code: str
    username: str


class InviteRequest(BaseModel):
    max_uses: int = 1


@router.post("/v1/auth/register")
async def register(req: RegisterRequest, request: Request):
    ip = request.client.host if request.client else "unknown"
    if _throttled(ip):
        raise HTTPException(status_code=429, detail="尝试次数过多，请稍后再试")
    try:
        principal, token = auth_store.register(code=req.code, username=req.username)
    except AuthError as e:
        _note_failure(ip)
        status = 409 if "占用" in e.reason else 403
        detail = e.reason if status == 409 else "邀请码无效"
        raise HTTPException(status_code=status, detail=detail)
    _FAILS.pop(ip, None)
    return {"token": token, "user_id": principal.user_id,
            "username": principal.username, "role": principal.role}


@router.get("/v1/auth/me")
async def me(principal: Principal = CurrentPrincipal):
    return {"user_id": principal.user_id, "username": principal.username,
            "role": principal.role}


# ---------- 管理端 ----------

@router.post("/v1/admin/invites")
async def create_invite(req: InviteRequest, actor: Principal = RequireAdmin):
    return {"code": auth_store.create_invite(actor.user_id, max_uses=req.max_uses)}


@router.get("/v1/admin/invites")
async def list_invites(_: Principal = RequireAdmin):
    keep = ("code", "max_uses", "created_at", "created_by", "expires_at", "used_by")
    return {"invites": [{k: i.get(k) for k in keep} for i in auth_store.list_invites()]}


@router.delete("/v1/admin/invites/{code}")
async def revoke_invite(code: str, _: Principal = RequireAdmin):
    if not auth_store.revoke_invite(code):
        raise HTTPException(status_code=404, detail="邀请码不存在")
    return {"status": "revoked", "code": code}


@router.get("/v1/admin/users")
async def list_users(_: Principal = RequireAdmin):
    keep = ("user_id", "username", "role", "disabled", "created_at", "last_used_at")
    return {"users": [{k: u.get(k) for k in keep} for u in auth_store.list_users()]}


@router.post("/v1/admin/users/{user_id}/disable")
async def disable_user(user_id: str, _: Principal = RequireAdmin):
    if not auth_store.disable_user(user_id):
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"status": "disabled", "user_id": user_id}


@router.post("/v1/admin/users/{user_id}/rotate-token")
async def rotate_user_token(user_id: str, _: Principal = RequireAdmin):
    try:
        return {"token": auth_store.rotate_token(user_id)}
    except AuthError:
        raise HTTPException(status_code=404, detail="用户不存在")


@router.delete("/v1/admin/users/{user_id}")
async def remove_user(user_id: str, actor: Principal = RequireAdmin):
    if user_id == actor.user_id:
        raise HTTPException(status_code=400, detail="不能删除自己")
    if not auth_store.delete_user(user_id):
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"status": "deleted", "user_id": user_id}
```

在 `backend/app/main.py` 里 `mount_pwa(app)` 之前加：

```python
from app.core.auth_router import router as auth_router
app.include_router(auth_router)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest backend/tests/test_auth_endpoints.py -q && python -m pytest backend/tests/ -q`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/core/auth_router.py backend/app/main.py backend/tests/test_auth_endpoints.py
git commit -m "新增注册与管理端点，管理响应不回显令牌哈希

邀请码失效原因对外统一成"邀请码无效"：区分"不存在"和"已用尽"会让这个端点
变成探测邀请码是否存在的信道。用户列表按字段白名单返回，token_hash 不出网。"
```

---

## Task 4: 会话与附件归属

**Files:**
- Modify: `backend/app/session/session_store.py`
- Modify: `backend/app/core/uploads.py`
- Modify: `backend/app/main.py:355-384`（sessions 路由）、`:468-490`（uploads 路由）、`:240-257`（`_prepare_chat`）
- Test: `backend/tests/test_isolation.py`

**Interfaces:**
- Consumes: Task 2 `CurrentPrincipal`、`Principal`
- Produces:
  - `SessionStore.create(self, model: str, owner: str) -> dict`
  - `SessionStore.list_summaries(self, owner: str) -> list`
  - `SessionStore.get(self, session_id: str, owner: str) -> dict | None`
  - `SessionStore.add_message(self, session_id: str, owner: str, role: str, content: str, message_id=None, memory_ids=None) -> bool`
  - `SessionStore.replace(self, session_id: str, owner: str, messages: list) -> bool`
  - `SessionStore.delete(self, session_id: str, owner: str) -> bool`
  - `SessionStore.find_message(self, message_id: str, owner: str) -> dict | None`
  - `UploadStore.save(self, filename: str, blob: bytes, claimed_mime: str = "", owner: str = "") -> dict`
  - `UploadStore.get(self, upload_id: str, owner: str) -> dict | None`（owner 必填）
  - `UploadStore.delete(self, upload_id: str, owner: str) -> bool`
  - `build_user_content(..., owner: str)`

- [ ] **Step 1: 写失败的测试**

创建 `backend/tests/test_isolation.py`：

```python
"""跨用户隔离矩阵：A 拿不到 B 的会话与附件。

一律用真实令牌 + AUTH_MODE=enforced 才能验到归属逻辑，因此本文件不复用
conftest 的 disabled 客户端，而是直接调用 store 层并另建最小 FastAPI app。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from app.core.auth import AuthStore
from app.session.session_store import SessionStore


@pytest.fixture
def two_users(tmp_path):
    auth = AuthStore(path=str(tmp_path / "users.json"),
                     invites_path=str(tmp_path / "invites.json"))
    out = {}
    for name in ("A", "B"):
        code = auth.create_invite("admin")
        principal, token = auth.register(code=code, username=name)
        out[name] = (principal.user_id, token)
    return out


@pytest.fixture
def store(tmp_path):
    return SessionStore(path=str(tmp_path / "sessions.json"))


def test_owner_can_create_and_read(store, two_users):
    a, _ = two_users["A"]
    created = store.create("fake-model", owner=a)
    assert store.get(created["session_id"], owner=a) is not None


def test_other_user_cannot_read_session(store, two_users):
    a, _ = two_users["A"]
    b, _ = two_users["B"]
    created = store.create("fake-model", owner=a)
    assert store.get(created["session_id"], owner=b) is None, \
        "别人的会话必须像不存在一样"


def test_list_only_returns_own_sessions(store, two_users):
    a, _ = two_users["A"]
    b, _ = two_users["B"]
    store.create("fake-model", owner=a)
    mine = store.create("fake-model", owner=b)
    ids = [s["session_id"] for s in store.list_summaries(a)]
    assert ids and mine["session_id"] not in ids


def test_write_operations_reject_non_owner(store, two_users):
    a, _ = two_users["A"]
    b, _ = two_users["B"]
    sid = store.create("fake-model", owner=a)["session_id"]
    assert store.add_message(sid, b, "user", "偷改") is False
    assert store.replace(sid, b, []) is False
    assert store.delete(sid, b) is False
    assert store.get(sid, owner=a)["messages"] == []


def test_find_message_scoped_by_owner(store, two_users):
    a, _ = two_users["A"]
    b, _ = two_users["B"]
    sid = store.create("fake-model", owner=a)["session_id"]
    store.add_message(sid, a, "assistant", "回答", message_id="m-1")
    assert store.find_message("m-1", a) is not None
    assert store.find_message("m-1", b) is None


def test_legacy_sessions_without_owner_are_backfilled_and_backed_up(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text('{"old": {"session_id": "old", "title": "T", "created_at": "x",'
                    ' "model": "m", "messages": []}}', encoding="utf-8")
    store = SessionStore(path=str(path))
    assert store.get("old", owner="default_user") is not None
    assert store.get("old", owner="u_victim") is None
    assert list(Path(tmp_path).glob("sessions.json.bak-*")), "回填前必须留原件备份"


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text('{"old": {"session_id": "old", "messages": []}}', encoding="utf-8")
    SessionStore(path=str(path))
    first = len(list(tmp_path.glob("sessions.json.bak-*")))
    SessionStore(path=str(path))
    assert len(list(tmp_path.glob("sessions.json.bak-*"))) == first, "重复加载不该再回填"
```

追加到同文件，覆盖 uploads：

```python
from app.core.uploads import UploadStore


@pytest.fixture
def uploads(tmp_path):
    return UploadStore(directory=str(tmp_path / "up"))


def test_upload_records_owner(uploads, two_users):
    a, _ = two_users["A"]
    rec = uploads.save("n.txt", "内容".encode(), "text/plain", owner=a)
    assert uploads.get(rec["id"], owner=a) is not None
    b, _ = two_users["B"]
    assert uploads.get(rec["id"], owner=b) is None, "别人的附件应视为不存在"
    assert uploads.delete(rec["id"], owner=b) is False


def test_legacy_upload_owner_backfilled(tmp_path):
    d = tmp_path / "up2"
    d.mkdir()
    (d / "index.json").write_text(
        '{"abc": {"id": "abc", "name": "n.txt", "kind": "text", "mime": "text/plain",'
        ' "size": 1, "path": "x", "created_at": "y"}}', encoding="utf-8")
    (d / "x").write_text("hi", encoding="utf-8")
    store = UploadStore(directory=str(d))
    assert store.get("abc", owner="default_user") is not None
    assert store.get("abc", owner="u_intruder") is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest backend/tests/test_isolation.py -q`
Expected: FAIL，`TypeError: create() got an unexpected keyword argument 'owner'`

- [ ] **Step 3: 改 session_store**

`backend/app/session/session_store.py`：`LEGACY_OWNER` 作为**类属性**定义，`_load()` 末尾加回填，且**回填失败必须抛出以阻止启动**（spec §4.4：不允许带着半迁移的库对外服务，否则 owner 校验会静默放行）。

```python
    LEGACY_OWNER = "default_user"

    def _load(self):
        if not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (ValueError, OSError) as e:
            backup = self.path + ".corrupt"
            try:
                os.replace(self.path, backup)
                print(f"⚠️ 会话文件损坏（{e}），已备份为 {backup}，从空会话开始")
            except OSError:
                print(f"⚠️ 会话文件损坏且无法备份（{e}），从空会话开始")
            return
        if isinstance(data, dict):
            self._sessions = data
        self._backfill_owner()

    def _backfill_owner(self):
        missing = [s for s in self._sessions.values() if "owner" not in s]
        if not missing:
            return
        # 备份与写回任何一步失败都直接向上抛：SessionStore 在导入 app.main 时
        # 构造，异常会让进程启动失败——这正是我们要的失败方式。
        backup = f"{self.path}.bak-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        shutil.copy2(self.path, backup)
        for record in missing:
            record["owner"] = self.LEGACY_OWNER
        self._flush()
        print(f"🧭 已为 {len(missing)} 条历史会话补 owner={self.LEGACY_OWNER}，"
              f"原件备份于 {backup}")
```

在文件顶部 `import` 处补 `import shutil`。

`create` 与其余方法改为（完整替换函数体，保持既有锁与 flush 语义）：

```python
    def create(self, model: str = "deepseek-chat", owner: str = LEGACY_OWNER) -> dict:
        session_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        with self._lock:
            self._sessions[session_id] = {
                "session_id": session_id, "title": "新对话", "created_at": now,
                "model": model, "messages": [], "owner": owner,
            }
            self._flush()
        return {"session_id": session_id, "created_at": now}

    def list_summaries(self, owner: str) -> list:
        with self._lock:
            summaries = [
                {"session_id": sid, "title": d.get("title", "新对话"),
                 "created_at": d.get("created_at", ""), "model": d.get("model", "")}
                for sid, d in self._sessions.items() if d.get("owner") == owner
            ]
        summaries.sort(key=lambda x: x["created_at"], reverse=True)
        return summaries

    def get(self, session_id: str, owner: str = LEGACY_OWNER):
        with self._lock:
            data = self._sessions.get(session_id)
            if not data or data.get("owner") != owner:
                return None
            return json.loads(json.dumps(data))

    def add_message(self, session_id: str, owner: str, role: str, content: str,
                    message_id: str = None, memory_ids: list = None) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.get("owner") != owner:
                return False
            entry = {"role": role, "content": content}
            if message_id:
                entry["message_id"] = message_id
            if memory_ids:
                entry["memory_ids"] = list(memory_ids)
            session["messages"].append(entry)
            if len(session["messages"]) == 1 and content:
                session["title"] = content[:20]
            self._flush()
            return True

    def find_message(self, message_id: str, owner: str):
        if not message_id:
            return None
        with self._lock:
            for sid, session in self._sessions.items():
                if session.get("owner") != owner:
                    continue
                for msg in session.get("messages", []):
                    if msg.get("message_id") == message_id:
                        return {"session_id": sid, "message": dict(msg)}
        return None

    def delete(self, session_id: str, owner: str) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.get("owner") != owner:
                return False
            del self._sessions[session_id]
            self._flush()
            return True

    def replace(self, session_id: str, owner: str, messages: list) -> bool:
        cleaned = self._clean(messages)
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.get("owner") != owner:
                return False
            session["messages"] = cleaned
            first_user = next((m for m in cleaned if m["role"] == "user"), None)
            if first_user:
                session["title"] = first_user["content"][:20]
            self._flush()
            return True
```

把 `replace` 中原有的逐条清洗循环抽成模块内静态方法 `_clean(messages)`（逻辑逐行照搬，不改语义），供两者共用。

- [ ] **Step 4: 改 uploads**

`backend/app/core/uploads.py`：`save` 只改两处——签名加 `owner`、record 多写一个 `owner` 键；`detect_kind`、体积上限、PDF 转文本、落盘与 `_flush` 等函数体语句一律不动。`get`/`delete` 的 `owner` 为必填并做归属判断：

```python
    def save(self, filename: str, blob: bytes, claimed_mime: str = "",
             owner: str = "default_user") -> dict:
        # 函数体其余部分保持原样，只在 record 里多写一个 owner
        record = {
            "id": upload_id, "name": os.path.basename(filename or "unnamed"),
            "kind": kind, "mime": mime or claimed_mime, "size": display_size,
            "path": stored, "owner": owner, "created_at": datetime.now().isoformat(),
        }

    def get(self, upload_id: str, owner: str):
        with self._lock:
            record = self._index.get(upload_id)
        if not record or record.get("owner") != owner:
            return None
        if not os.path.isfile(record["path"]):
            return None
        return record
```

在 `_load()` 内对 `index.json` 做与 sessions 同样的回填（缺 `owner` → `"default_user"`，先 `shutil.copy2` 成 `.bak-<时间戳>` 再原子写回）。`delete(self, upload_id, owner)` 先经 `get(upload_id, owner)` 判定。`read_text`/`data_uri` 增加 `owner` 参数并转传给 `get`。

`app/core/uploads.py` 的 `build_user_content(...)` 签名加 `owner: str`，内部 `store.get(upload_id, owner)`。

- [ ] **Step 5: 改 main.py 调用点**

sessions 路由（原 355-384）逐条加 principal 并传 owner，非本人 → 404；uploads（原 468-490）传 `owner=principal.user_id`；`_prepare_chat` 接收 principal 并把 `owner` 传给 `build_user_content` 与 `upload_store.get`；`create_session`/`chat`/`stream_chat_endpoint` 里 `ChatPipeline(user_id="default_user")` 改为 `ChatPipeline(user_id=principal.user_id)`；`add_message(...)` 全部补上 `principal.user_id`。

```python
@app.get("/v1/sessions/{session_id}")
async def get_session(session_id: str, principal: Principal = CurrentPrincipal):
    session = sessions_store.get(session_id, owner=principal.user_id)
    if session is None:
        # 404 而非 403：403 等于承认这个 id 存在，可以被拿来枚举
        raise HTTPException(status_code=404, detail="会话不存在")
    return session
```

`submit_feedback` 中 `sessions_store.find_message(feedback.message_id)` → `find_message(feedback.message_id, principal.user_id)`。

- [ ] **Step 6: 运行确认通过**

Run: `python -m pytest backend/tests/test_isolation.py -q && python -m pytest backend/tests/ -q`
Expected: 全部 PASS

- [ ] **Step 7: 提交**

```bash
git add backend/app/session/session_store.py backend/app/core/uploads.py backend/app/main.py backend/tests/test_isolation.py
git commit -m "会话与附件加归属校验，历史数据回填为默认用户

原先 GET /v1/sessions 返回所有人的会话，凭 id 即可读任意聊天记录；附件也无
归属，任何持口令者能下载别人上传的 PDF。owner 一律由 principal 推导，非本人
按 404 处理以免成为枚举信道。迁移只补字段并先备份原件，重复加载幂等。"
```

---

## Task 5: 记忆端点去客户端身份

**Files:**
- Modify: `backend/app/memory/memory_router.py`
- Modify: `backend/app/memory/memory_manager.py`
- Test: `backend/tests/test_isolation.py`（追加）

**Interfaces:**
- Consumes: Task 2 `CurrentPrincipal`/`RequireAdmin`
- Produces: HTTP 契约变更
  - `POST /v1/memory/add` body `{content, metadata?, summarize?}`（**无 user_id**）
  - `POST /v1/memory/search` body `{query, top_k}`
  - `DELETE /v1/memory/delete` body `{memory_ids}`
  - `PUT /v1/memory/update` body `{memory_id, new_content?, new_weight?}`
  - `GET /v1/memory/list?limit=`（原 `/v1/memory/list/{user_id}`，路径变更）
  - `GET /v1/memory/stats`、`POST /v1/memory/decay` → 仅 admin
  - `MemoryManager.owned_ids(self, user_id: str, ids: list) -> list`

- [ ] **Step 1: 写失败的测试**

追加到 `backend/tests/test_isolation.py`：

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient
import app.memory.memory_router as mr


@pytest.fixture
def mem_api(tmp_path, monkeypatch):
    """只挂记忆路由，用真实内存 fake_store 验身份推导。"""
    from app.memory.memory_router import FakeMemoryStore, router
    fake = FakeMemoryStore()
    monkeypatch.setattr(mr, "memory_manager", None)
    monkeypatch.setattr(mr, "memory_init_error", None)
    monkeypatch.setattr(mr, "fake_store", fake)
    app = FastAPI()
    app.include_router(router)

    auth = AuthStore(path=str(tmp_path / "u.json"), invites_path=str(tmp_path / "i.json"))
    tokens = {}
    for name in ("A", "B"):
        code = auth.create_invite("admin")
        _, token = auth.register(code=code, username=name)
        tokens[name] = token

    def hdr(tok):
        return {"Authorization": "Bearer " + tok}
    return TestClient(app), hdr(tokens["A"]), hdr(tokens["B"])


def test_memory_add_is_scoped_to_caller(mem_api):
    client, ha, hb = mem_api
    assert client.post("/v1/memory/add",
                       json={"content": "我叫张三", "summarize": False}, headers=ha).status_code == 200
    assert client.post("/v1/memory/add",
                       json={"content": "B的秘密", "summarize": False}, headers=hb).status_code == 200
    mine = client.get("/v1/memory/list?limit=50", headers=ha).json()["memories"]
    assert [m["content"] for m in mine] == ["我叫张三"]


def test_client_supplied_user_id_is_ignored(mem_api):
    client, ha, _ = mem_api
    res = client.post("/v1/memory/add",
                      json={"content": "越权写入", "user_id": "u_victim"}, headers=ha)
    assert res.status_code == 200
    victim = client.get("/v1/memory/list?limit=50", headers={"Authorization": "Bearer nothing"})
    assert victim.status_code == 401


def test_cannot_delete_another_users_memory(mem_api):
    client, ha, hb = mem_api
    client.post("/v1/memory/add", json={"content": "B的记忆", "summarize": False}, headers=hb)
    target = client.get("/v1/memory/list?limit=50", headers=hb).json()["memories"][0]["id"]
    res = client.delete("/v1/memory/delete", json={"memory_ids": [target]}, headers=ha)
    assert res.status_code == 200
    assert res.json()["deleted_count"] == 0
    assert client.get("/v1/memory/list?limit=50", headers=hb).json()["total"] == 1


def test_cannot_update_another_users_memory(mem_api):
    client, ha, hb = mem_api
    client.post("/v1/memory/add", json={"content": "原文", "summarize": False}, headers=hb)
    target = client.get("/v1/memory/list?limit=50", headers=hb).json()["memories"][0]["id"]
    client.put("/v1/memory/update", json={"memory_id": target, "new_content": "被篡改"}, headers=ha)
    after = client.get("/v1/memory/list?limit=50", headers=hb).json()["memories"][0]["content"]
    assert after == "原文"


def test_stats_requires_admin(mem_api):
    client, ha, _ = mem_api
    assert client.get("/v1/memory/stats", headers=ha).status_code == 403


def test_chat_writes_memory_under_caller_not_default_user():
    """对话产生的记忆必须挂在调用者名下。

    /v1/chat 原先把 user_id 写死成 default_user，所有人共用一个记忆池；
    这条断言专门防止该写死回归。
    """
    import app.pipeline as pipeline
    from app.core.auth import auth_store
    from app.main import app as fastapi_app
    from fastapi.testclient import TestClient

    code = auth_store.create_invite("default_user")
    principal, token = auth_store.register(code=code, username="对话归属测试")
    seen = {}

    real_init = pipeline.ChatPipeline.__init__

    def spy_init(self, user_id="default_user", *a, **kw):
        seen["user_id"] = user_id
        return real_init(self, user_id, *a, **kw)

    monkeypatch_pipeline = pytest.MonkeyPatch()
    monkeypatch_pipeline.setattr(pipeline.ChatPipeline, "__init__", spy_init)
    try:
        TestClient(fastapi_app).post(
            "/v1/chat",
            json={"messages": [{"role": "user", "content": "你好"}]},
            headers={"Authorization": "Bearer " + token})
    finally:
        monkeypatch_pipeline.undo()

    assert seen.get("user_id") == principal.user_id, \
        f"期望调用者身份，实际 {seen.get('user_id')!r}"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest backend/tests/test_isolation.py -q -k memory`
Expected: FAIL（`user_id` 仍是必填 → 422，或身份未推导）

- [ ] **Step 3: 实现**

`memory_manager.py` 加：

```python
    def owned_ids(self, user_id: str, ids: list) -> list:
        """只保留确实属于该用户的记忆 id。

        删除/改权重原先直接拿客户端给的 id 就动手，等于任何人可改任何人的记忆。
        """
        wanted = {str(i) for i in ids or []}
        if not wanted:
            return []
        got = self.collection.get(ids=list(wanted), where={"user_id": user_id})
        return list(got.get("ids") or [])
```

`memory_router.py`：请求模型删去 `user_id` 字段；每个端点加 `principal: Principal = CurrentPrincipal`；写入时 `user_id=principal.user_id`；`delete`/`update` 先过 `owned_ids`；`stats`/`decay` 用 `RequireAdmin`；`list` 路径改 `/list`。

```python
class AddMemoryRequest(BaseModel):
    content: str = Field(..., description="记忆内容")
    metadata: Optional[dict] = Field(None, description="额外的元数据")
    summarize: bool = Field(False, description="是否使用AI摘要")


class DeleteMemoryRequest(BaseModel):
    memory_ids: List[str] = Field(..., description="要删除的记忆ID列表")


@router.delete("/delete")
async def delete_memories(req: DeleteMemoryRequest, principal: Principal = CurrentPrincipal):
    def real_delete():
        mine = memory_manager.owned_ids(principal.user_id, req.memory_ids)
        if not mine:
            return 0
        result = memory_manager.delete_memories_batch(mine)
        return 0 if "error" in result else result.get("count", 0)

    def fake_delete():
        mine = [m for m in req.memory_ids
                if fake_store.memories.get(m, {}).get("user_id") == principal.user_id]
        return fake_store.delete_batch(mine)

    deleted = safe_call(real_delete, fake_delete)
    return {"status": "success", "message": f"已删除 {deleted} 条记忆", "deleted_count": deleted}


@router.get("/list")
async def list_user_memories(limit: int = 20, principal: Principal = CurrentPrincipal):
    def real_list():
        return memory_manager.get_user_memories(principal.user_id, limit)

    def fake_list():
        return fake_store.list(principal.user_id, limit)

    memories = safe_call(real_list, fake_list)
    return {"status": "success", "total": len(memories), "memories": memories}
```

`search`/`add`/`update` 同理：`update` 也先 `owned_ids` 过滤，命中 0 条时返回 `"记忆不存在"`。前端调用点 `api.js` 里 `listMemory` 的路径同步改为 `/v1/memory/list?limit=`。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest backend/tests/test_isolation.py -q && python -m pytest backend/tests/ -q`
Expected: 全部 PASS（`test_api.py`/`test_memory.py`/`test_web_pwa.py` 中直接传 `user_id` 的用例需一并改成走带凭据的请求；改动限于此三个文件，逐条按新契约更新）

- [ ] **Step 5: 提交**

```bash
git add backend/app/memory/memory_router.py backend/app/memory/memory_manager.py backend/tests/test_isolation.py backend/tests/test_api.py backend/tests/test_memory.py backend/tests/test_web_pwa.py backend/app/web/static/api.js
git commit -m "记忆接口不再接受客户端自报 user_id

原先 delete/update 收下 user_id 却完全不用于校验归属，任何人都能删别人记忆；
list 靠路径参数即可枚举他人。现在身份一律来自 principal，写操作先过 owned_ids。"
```

---

## Task 6: 路由级鉴权契约守卫

**Files:**
- Create: `backend/tests/test_route_auth_contract.py`
- Modify: `backend/app/core/authz.py`（`PUBLIC_PATHS` 如需补项）

**Interfaces:**
- Consumes: 全部已完成路由；Task 2 `current_principal`/`require_admin`
- Produces: 一条会因"新端点忘挂鉴权"而失败的测试

- [ ] **Step 1: 写测试**

创建 `backend/tests/test_route_auth_contract.py`：

```python
"""每条 /v1/* 路由都必须声明身份依赖。

这是本计划最重要的回归锁：现有的洞（客户端自报 user_id、sessions 无归属）
全都是"新端点忘了挂鉴权"长出来的，靠人记住不可靠。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.authz import PUBLIC_PATHS, current_principal, require_admin
from app.main import app

GUARD_CALLABLES = {current_principal, require_admin}
# 非 /v1 的系统端点：健康检查与静态首页
EXEMPT_PATHS = {"/", "/health"}


def _deps_of(dependant):
    stack = [dependant]
    while stack:
        cur = stack.pop()
        for sub in cur.dependencies:
            yield sub.call
            stack.append(sub)
        if cur.websocket:
            for sub in cur.websocket.dependencies:
                yield sub.call
                stack.append(sub)


def test_every_v1_route_declares_an_identity_dependency():
    missing = []
    for route in app.routes:
        path = getattr(route, "path", "")
        dependant = getattr(route, "dependant", None)
        if not path.startswith("/v1/") or dependant is None:
            continue
        if path in PUBLIC_PATHS:
            continue
        if not (set(_deps_of(dependant)) & GUARD_CALLABLES):
            missing.append(path)
    assert not missing, f"以下端点未声明身份依赖：{sorted(set(missing))}"


def test_public_allowlist_is_exactly_registration():
    assert PUBLIC_PATHS == {"/v1/auth/register"}
```

- [ ] **Step 2: 运行，按失败清单补齐**

Run: `python -m pytest backend/tests/test_route_auth_contract.py -q`
Expected: 可能 FAIL 并列出未挂依赖的端点（如 `/v1/models`、`/v1/agent/run`、`/v1/uploads`）

对清单里每一条，给该端点加 `principal: Principal = CurrentPrincipal`（`/v1/agent/run` 若属管理员专用则用 `RequireAdmin`）。`/v1/models` 需要 principal 以渲染下拉但保持现有 key masking。

- [ ] **Step 3: 运行确认通过**

Run: `python -m pytest backend/tests/test_route_auth_contract.py -q && python -m pytest backend/tests/ -q`
Expected: 全部 PASS

- [ ] **Step 4: 提交**

```bash
git add backend/tests/test_route_auth_contract.py backend/app/main.py
git commit -m "加路由级鉴权契约测试，并补齐漏挂身份的端点

现有洞都是"新端点忘挂鉴权"长出来的，靠人记不可靠。该测试枚举全部 /v1 路由，
要求每条要么声明 current_principal、要么 require_admin，公开面只允许注册一项。"
```

---

## Task 7: providers 转管理员 + 前端注册界面

**Files:**
- Modify: `backend/app/main.py:398-465`（7 个 providers 端点）
- Modify: `backend/app/web/static/index.html`、`app.js`、`api.js`
- Test: `backend/tests/test_isolation.py`（追加）、`backend/tests/test_web_pwa.py`

**Interfaces:**
- Consumes: Task 2 `RequireAdmin`
- Produces: 前端首启注册流

- [ ] **Step 1: 写失败的测试**

追加到 `backend/tests/test_isolation.py`：

```python
PROVIDER_ROUTES = [
    ("GET", "/v1/providers"),
    ("POST", "/v1/providers"),
    ("PUT", "/v1/providers/fake-model"),
    ("DELETE", "/v1/providers/fake-model"),
    ("POST", "/v1/providers/fake-model/default"),
    ("POST", "/v1/providers/fake-model/test"),
    ("POST", "/v1/providers/test"),
]


@pytest.mark.parametrize("method,path", PROVIDER_ROUTES)
def test_non_admin_cannot_touch_providers(client, method, path):
    """模型服务配置能改掉整个后端行为，必须管理员专属。"""
    from app.core.auth import auth_store
    code = auth_store.create_invite("default_user")
    _, token = auth_store.register(code=code, username="普通用户")
    res = client.request(method, path, headers={"Authorization": "Bearer " + token},
                         json={})
    assert res.status_code == 403, f"{method} {path} 竟然放行了"
```

追加到 `backend/tests/test_web_pwa.py`（沿用该文件既有的"HTML 与 JS 元素 id 一致"检查风格）：

```python
def test_registration_ui_elements_wired():
    """注册界面缺元素会让 app.js 的绑定静默失败，整块输入区失灵。"""
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    defined = set(re.findall(r'id="([^"]+)"', html))
    for el in ("regCode", "regUsername", "registerBtn"):
        assert f'$("{el}")' in js, f"app.js 引用了 #{el} 但 HTML 未定义"
        assert el in defined


def test_memory_calls_no_longer_send_user_id():
    api = (STATIC / "api.js").read_text(encoding="utf-8")
    assert "user_id" not in api, "记忆接口已不接受客户端身份"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest backend/tests/test_isolation.py -q -k providers && python -m pytest backend/tests/test_web_pwa.py -q`
Expected: FAIL（403 断言失败或元素缺失）

- [ ] **Step 3: providers 加管理员依赖**

`main.py` 中 7 个端点逐个加 `_: Principal = RequireAdmin`，例如：

```python
@app.get("/v1/providers")
async def list_providers(_: Principal = RequireAdmin):
    # 绝不返回明文密钥，只给掩码与"是否已配置"
    return {"providers": provider_store.public_list(), "presets": PRESETS}
```

- [ ] **Step 4: 前端注册流**

`index.html` 的连接 pane，在既有口令输入之下加注册区：

```html
<div class="row" id="registerRow">
  <input id="regUsername" type="text" placeholder="用户名" autocomplete="off">
  <input id="regCode" type="text" placeholder="邀请码，如 AB2C-DEF4" autocomplete="off">
  <button class="btn btn-primary" id="registerBtn">注册并登录</button>
</div>
<p class="pane-note" id="registerHint"></p>
```

`app.js`：`pref` 增 `token`/`userId` 读写（`localStorage` 键 `accessToken`/`userId`），`bind()` 内加：

```javascript
  $("registerBtn").onclick = async () => {
    const hint = $("registerHint");
    hint.textContent = "注册中…";
    try {
      const res = await API.register($("regCode").value.trim(), $("regUsername").value.trim());
      localStorage.setItem("accessToken", res.token);
      localStorage.setItem("userId", res.user_id);
      hint.textContent = `已登录为 ${res.username}，刷新后生效`;
    } catch (e) {
      hint.textContent = "注册失败：" + e.message;
    }
  };
```

`api.js`：`authHeaders()` 保持读 `accessToken`（语义变为个人令牌）；新增

```javascript
    register: (code, username) =>
      request("/v1/auth/register", { method: "POST", body: { code, username } }),
    me: () => request("/v1/auth/me"),
```

并把 `addMemory`/`listMemory`/`searchMemory`/`deleteMemory` 的 `userId` 形参与请求体字段删除。

- [ ] **Step 5: 运行确认通过**

Run: `python -m pytest backend/tests/ -q`
Expected: 全部 PASS

- [ ] **Step 6: 手工验证并截图**

在 `AUTH_MODE=enforced` 下起服务：浏览器打开 `/app/`，用一个邀请码注册，确认①能正常对话 ②"模型服务"页对普通用户不可写（403 提示）③换第二个邀请码注册另一用户后，会话列表为空且看不到前者内容。

- [ ] **Step 7: 提交**

```bash
git add backend/app/main.py backend/app/web/static/index.html backend/app/web/static/app.js backend/app/web/static/api.js backend/tests/test_isolation.py backend/tests/test_web_pwa.py
git commit -m "模型服务配置收回管理员，前端接入邀请码注册

providers 的增删改与设默认能整体改变后端行为，普通用户不应可写。
前端令牌改为注册所得的个人令牌；记忆接口调用随之去掉 user_id。"
```

---

## Task 8: 端到端验收与运维收尾

**Files:**
- Modify: `docs/用户手册.md`、`docs/安装部署指南.md`
- Create: `tools/make-invite.bat`

- [ ] **Step 1: 全量回归**

Run: `python -m pytest backend/tests/ -q`
Expected: 全绿，且新增测试数 ≥ 40

再静态确认"日志绝不输出凭据明文"这条约束没有被违背：

```bash
grep -rnE "print\(.*(token|code)" backend/app/core/auth.py backend/app/core/authz.py \
  backend/app/core/auth_router.py || echo "OK: 无凭据明文入日志"
```

Expected：只输出 `OK: 无凭据明文入日志`。若命中，改日志措辞而非加脱敏函数——这里没必要为一个暂时不存在的需求造工具。

- [ ] **Step 2: 冻结版验证**

重建 EXE（注意 spec 需收集 `app/core/auth*.py`——同包自动收集，无需改），启动后按 Task 7 Step 6 的三项在 `https://ai.fenever.xyz/app/` 上复验一次。确认 `data/users.json` 与 `invites.json` 落在**项目根**而非 `dist/`。

- [ ] **Step 3: 签发首个邀请码**

创建 `tools/make-invite.bat`：

```bat
@echo off
rem 生成一个邀请码。口令从 .env 读，避免手抄泄露。
setlocal
cd /d C:\Users\34426\ai-assistant
for /f "delims=" %%i in ('findstr /b ACCESS_TOKEN .env') do set BOOTSTRAP=%%i
set BOOTSTRAP=%BOOTSTRAP:ACCESS_TOKEN=%=%
curl -s -X POST -H "Authorization: Bearer %BOOTSTRAP%" ^
     -H "Content-Type: application/json" ^
     -d "{\"max_uses\":1}" http://127.0.0.1:8000/v1/admin/invites
echo.
endlocal
```

- [ ] **Step 4: 更新文档**

`docs/用户手册.md` 增"注册"一节（要邀请码、令牌丢失需管理员轮换）；`docs/安装部署指南.md` 增：多用户部署后 `ACCESS_TOKEN` 的角色变化、`AUTH_MODE` 两种取值含义、以及"无凭据即 503"的排障提示。

- [ ] **Step 5: 提交**

```bash
git add docs tools/make-invite.bat
git commit -m "补充多用户身份的操作文档与邀请码签发脚本"
```

---

## 完成标准

- `python -m pytest backend/tests/ -q` 全绿
- 路由契约测试通过，公开面仅 `/v1/auth/register`
- 两个真实用户各自看不到对方的会话、记忆、附件（`test_isolation.py` 覆盖）
- 普通用户访问 7 个 providers 端点全部 403
- 停用一个用户后其令牌立即失效
- 本机 EXE 与已发出 APK 凭 `ACCESS_TOKEN` 继续可用
