# backend/tests/test_review_fixes_20260923.py
"""2026-09-22 全量审查后的一批修复的回归锁。

四条 High 与两个正确性 bug，每个都钉一条"改回原样就会红"的用例：
- 工具参数必须按 schema 白名单过滤（max_retries 这类签名内形参模型不许给）；
- 上传在进内存前先过硬上限（原实现是先全量读进内存才分档校验）；
- 慢哈希（bcrypt）不许压在身份库全局锁里跑（会把事件循环上的鉴权中间件
  连整个进程冻住几百毫秒）；
- 更新 provider 不带 paid_by 时保留原计费归属（原实现每次 PUT 静默翻成
  operator，账本变假账）；
- 模型吐残缺 JSON 工具参数只毒那一次工具调用，不炸整轮对话。
"""
import threading
import time

import pytest

from app.tools import registry
from app.tools.registry import tools_registry
from app.tools.executor import execute_tool


# ---------- 1. 工具参数按 schema 白名单过滤 ----------

def _add_spy():
    seen = {}

    def _spy(code, max_retries=2):
        seen["max_retries"] = max_retries
        return "ok"

    registry.register_tool(
        name="_spy_schema_filter", description="探针工具",
        parameters={"type": "object",
                    "properties": {"code": {"type": "string"}},
                    "required": ["code"]})(_spy)
    return seen


def test_params_outside_schema_are_dropped_before_call():
    """execute_code 的真签名有 max_retries，但 schema 只声明 code/language：
    模型传 max_retries=100 必须在 _dispatch 被丢掉，不然就是串行起上百个容器。"""
    seen = _add_spy()
    try:
        out = execute_tool("_spy_schema_filter", {"code": "x", "max_retries": 100})
        assert seen["max_retries"] == 2, "schema 外的参数透传进了函数"
        assert "ok" in out
    finally:
        tools_registry.pop("_spy_schema_filter", None)


def test_non_dict_arguments_become_structured_error_not_crash():
    seen = _add_spy()
    try:
        out = execute_tool("_spy_schema_filter", ["not", "a", "dict"])
        assert "参数错误" in out
    finally:
        tools_registry.pop("_spy_schema_filter", None)


# ---------- 2. 上传的进内存前硬上限 ----------

def test_oversized_upload_is_refused_before_full_read(client):
    """原实现 `file.file.read()` 无上限，save() 的分档校验在拿到完整 blob 之后
    才跑——用一个任意文件名就能把进程顶爆内存。现在读满上限+1 字节即 413。"""
    from app.core.uploads import MAX_UPLOAD_BYTES
    big = b"x" * (MAX_UPLOAD_BYTES + 1024)
    res = client.post("/v1/uploads", files={"file": ("big.txt", big, "text/plain")})
    assert res.status_code == 413
    assert "上限" in res.json()["detail"]


def test_upload_within_cap_still_follows_per_kind_rules(client):
    """硬上限只挡最大的那一档；1MB 的 .txt 仍要走 save() 原有的文本档 400。"""
    res = client.post("/v1/uploads",
                      files={"file": ("notes.txt", b"y" * (2 * 1024 * 1024),
                                      "text/plain")})
    assert res.status_code == 400
    assert "超过上限" in res.json()["detail"]


# ---------- 3. bcrypt 不许压在身份库全局锁里 ----------

def test_slow_bcrypt_does_not_block_resolve(tmp_path, monkeypatch):
    """事故形状：登录/找回在持锁期间跑几百毫秒 bcrypt，而鉴权中间件在事件循环
    上同步调 resolve/has_role 抢同一把锁 → 一次登录冻住整个进程。
    修法是把慢哈希挪到锁外。这条用例把 bcrypt 钉慢 0.4s，另一头 resolve 必须
    照常毫秒级返回。"""
    import app.core.auth as auth_mod
    from app.core.auth import AuthStore

    store = AuthStore(path=str(tmp_path / "users.json"))
    _, token = store.register(username="alice", password="correct-horse-battery",
                              security_answers=["新市场小学", "hehai2024", "李建国"])

    real_check = auth_mod._check_password
    in_bcrypt = threading.Event()

    def slow_check(password, stored):
        in_bcrypt.set()
        time.sleep(0.4)
        return real_check(password, stored)

    monkeypatch.setattr(auth_mod, "_check_password", slow_check)

    errors = []

    def do_login():
        try:
            store.login("alice", "wrong-horse-battery")
        except auth_mod.AuthError:
            pass
        except Exception as e:  # noqa: BLE001 - 断言失败形状，原样收集
            errors.append(e)

    t = threading.Thread(target=do_login)
    t.start()
    assert in_bcrypt.wait(2.0), "登录没有走到慢哈希那一步"
    # 慢哈希正在跑的那一拍里问一次鉴权：锁没被占则毫秒级返回。
    t0 = time.monotonic()
    principal = store.resolve(token)
    waited = time.monotonic() - t0
    t.join()
    assert not errors
    assert principal is not None and principal.username == "alice"
    assert waited < 0.3, f"resolve 被锁在 bcrypt 后面等了 {waited:.2f}s，锁内慢哈希回归"


# ---------- 4. 更新 provider 不带 paid_by 时保留原归属 ----------

def _provider_payload(**over):
    base = {"id": "p1", "label": "L", "base_url": "https://example.invalid/v1",
            "api_key": "sk-test-000111222333", "model": "m", "is_default": True}
    return {**base, **over}


def test_upsert_keeps_paid_by_when_update_omits_it(tmp_path):
    """PUT /v1/providers/{id} 的请求模型没有 paid_by 字段；原实现每次都让
    _validate 兜底成 operator——用户自带 key 的账改一次配置就翻成管理员垫钱。"""
    from app.core.providers import ProviderStore
    ps = ProviderStore(path=str(tmp_path / "providers.json"))
    ps.upsert(_provider_payload(paid_by="user"))
    updated = ps.upsert(_provider_payload(paid_by=None, label="改名"))
    assert updated["paid_by"] == "user"
    # 显式传值仍然允许改：保留只针对"没传"这一支。
    moved = ps.upsert(_provider_payload(paid_by="operator"))
    assert moved["paid_by"] == "operator"


# ---------- 5. 残缺 JSON 工具参数只毒那一次调用 ----------

class _FakeFunc:
    def __init__(self, name, arguments):
        self.name, self.arguments = name, arguments


class _FakeCall:
    def __init__(self, id, name, arguments):
        self.id, self.function = id, _FakeFunc(name, arguments)


class _FakeMsg:
    def __init__(self, content=None, tool_calls=None):
        self.content, self.tool_calls = content, tool_calls

    def model_dump(self):
        d = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = [{"id": c.id, "type": "function",
                                "function": {"name": c.function.name,
                                             "arguments": c.function.arguments}}
                               for c in self.tool_calls]
        return d


def test_malformed_tool_arguments_do_not_kill_the_turn(monkeypatch):
    """模型吐半截 JSON 很常见；原来 json.loads 在内层 try 之外，一次坏参数 =
    /v1/chat 502、本轮全丢。现在应作为工具错误回填，循环继续到收尾回答。"""
    import app.pipeline as pipeline

    responses = [
        _FakeMsg(tool_calls=[_FakeCall("c1", "calculator", '{"exppress": 1+')]),
        _FakeMsg(content="收尾回答"),
    ]

    class _Completions:
        def create(self, **kwargs):
            return type("R", (), {"choices": [type("Ch", (),
                    {"message": responses.pop(0)})()],
                    "usage": None})()

    monkeypatch.setattr(pipeline, "build_client",
                        lambda provider: type("C", (), {"chat": type(
                            "X", (), {"completions": _Completions()})()})())

    p = pipeline.ChatPipeline(user_id="tester")
    p.tools_schema = []  # 不测工具注册，只测解析边界
    answer = p._call_model_with_tool_loop(
        "fake-chat", [{"role": "user", "content": "算一下"}])
    assert answer == "收尾回答"
    assert not responses, "第二轮没有被调用——循环被坏参数炸断了"
