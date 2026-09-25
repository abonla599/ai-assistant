"""个人模型服务（/v1/me/providers）与 owner 归属边界测试。

判据对应 plan-user-providers-20260923.md 的 AC-1..AC-5：
用户能自带 key 接入私有模型；私有条目对其他人（含管理员）等于不存在；
默认选择持久化在服务端并可回落。密钥出口纪律由 test_secret_scan 兜底，
这里另断言接口响应体本身不回显明文。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

MY_KEY = "sk-user-private-key-999"  # secret-scan:allow 测试里造的假密钥，这台机器上没有这颗 key


def _payload(**over):
    base = {"label": "我的私有模型", "base_url": "https://api.mine.invalid/v1",
            "api_key": MY_KEY, "model": "my-chat", "supports_vision": False}
    base.update(over)
    return base


def _add(client, hdr, **over):
    r = client.post("/v1/me/providers", json=_payload(**over), headers=hdr)
    assert r.status_code == 200, r.text
    return r.json()["provider"]["id"]


def _chat(client, hdr, provider=None):
    body = {"messages": [{"role": "user", "content": "你好"}]}
    if provider:
        body["provider"] = provider
    return client.post("/v1/chat", json=body, headers=hdr)


# ---------- AC-1：普通用户添加并用自己的 provider 聊天 ----------

def test_user_can_add_own_provider_and_chat_with_it(client, enforced):
    a = enforced("provider-owner-a")
    pid = _add(client, a)
    assert MY_KEY not in client.get("/v1/me/providers", headers=a).text

    models = client.get("/v1/models", headers=a).json()["models"]
    entry = next(m for m in models if m["id"] == pid)
    assert entry["usable"] is True
    assert entry["shared"] is False, "自己的私有条目不该标成共享"

    r = _chat(client, a, provider=pid)
    assert r.status_code == 200, r.text
    assert r.json()["provider"] == pid
    assert r.json()["model"] == "my-chat"


def test_placeholder_key_saves_but_marks_unusable(client, enforced):
    a = enforced("placeholder-user")
    pid = _add(client, a, api_key="your-key-here")
    mine = client.get("/v1/me/providers", headers=a).json()["mine"]
    entry = next(p for p in mine if p["id"] == pid)
    assert entry["has_key"] is False
    models = client.get("/v1/models", headers=a).json()["models"]
    assert next(m for m in models if m["id"] == pid)["usable"] is False


# ---------- AC-2：用户间隔离，越权与不存在同一种失败 ----------

def test_private_provider_invisible_and_unusable_to_other_user(client, enforced):
    a = enforced("private-owner-a")
    b = enforced("private-owner-b")
    pid = _add(client, a)

    ids_b = [m["id"] for m in client.get("/v1/models", headers=b).json()["models"]]
    assert pid not in ids_b
    mine_b = client.get("/v1/me/providers", headers=b).json()
    assert pid not in [p["id"] for p in mine_b["mine"]]

    # B 拿 A 的 id 聊天：与"不存在的 id"同一行为——回落默认，绝不改道到 A 的上游
    r = _chat(client, b, provider=pid)
    assert r.status_code == 200, r.text
    assert r.json()["provider"] != pid
    assert r.json()["model"] != "my-chat"

    # B 改/删/测/设默认 A 的条目：全是 404（与不存在的 id 无差别）
    assert client.put(f"/v1/me/providers/{pid}", json=_payload(label="劫持"), headers=b).status_code == 404
    assert client.delete(f"/v1/me/providers/{pid}", headers=b).status_code == 404
    assert client.post(f"/v1/me/providers/{pid}/test", headers=b).status_code == 404
    assert client.post("/v1/me/providers/default", json={"provider_id": pid}, headers=b).status_code == 404


def test_private_provider_never_becomes_site_default(client, enforced):
    a = enforced("private-not-default")
    c = enforced("bystander-c")
    # 断言写成"没被顶掉"而不是"等于 fake-model"：整套跑下来别的用例可能动过
    # 全站默认（★ 被点在谁头上、甚至谁被删了），写死具体 id 的断言会在离真凶
    # 很远的地方假红。这一条要守的不变式只有：加一条私有配置前后，旁观者看到
    # 的默认一个子都不变。
    base = client.get("/v1/models", headers=c).json()["default"]
    pid = _add(client, a)
    got = client.get("/v1/models", headers=c).json()["default"]
    assert got == base, f"旁观者的默认被别人的私有配置顶掉了：{base} -> {got}"
    assert got != pid


# ---------- AC-3：管理员面只见共享、不碰私有 ----------

def test_admin_surface_never_sees_or_touches_private(client, enforced):
    a = enforced("admin-blindness-owner")
    pid = _add(client, a)
    admin = {"Authorization": "Bearer boot-token"}

    ids = [p["id"] for p in client.get("/v1/providers", headers=admin).json()["providers"]]
    assert pid not in ids, "管理员面不得暴露私有条目存在性"
    assert client.put(f"/v1/providers/{pid}", json=_payload(label="转公"), headers=admin).status_code == 404
    assert client.delete(f"/v1/providers/{pid}", headers=admin).status_code == 404
    assert client.post(f"/v1/providers/{pid}/default", headers=admin).status_code == 404
    assert client.post(f"/v1/providers/{pid}/test", headers=admin).status_code == 404


# ---------- AC-4：校验错误不回显密钥；草稿试连不落盘 ----------

def test_validation_errors_never_echo_key(client, enforced):
    a = enforced("bad-input-user")
    r = client.post("/v1/me/providers", json=_payload(base_url="ftp://nope"), headers=a)
    assert r.status_code == 400
    assert MY_KEY not in r.text
    r = client.post("/v1/me/providers", json=_payload(model=""), headers=a)
    assert r.status_code == 400
    assert MY_KEY not in r.text


def test_draft_ping_does_not_persist(client, enforced, monkeypatch):
    a = enforced("draft-user")
    calls = {}

    class _Create:
        def create(self, **kw):
            calls["model"] = kw.get("model")
            return type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": "pong"})()})]})()

    class _Client:
        chat = type("Ch", (), {"completions": _Create()})()

    monkeypatch.setattr("app.main.build_client", lambda p, **kw: _Client())
    r = client.post("/v1/me/providers/test", json=_payload(), headers=a)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert calls["model"] == "my-chat"
    mine = client.get("/v1/me/providers", headers=a).json()["mine"]
    assert mine == [], "草稿试连不许留下任何已存配置"


# ---------- AC-5：「我的默认」服务端持久化与回落 ----------

def test_my_default_persists_and_falls_back(client, enforced):
    a = enforced("pref-user")
    # 新用户还没有偏好，此刻看到的默认就是全局默认。回落断言用它而不是写死
    # "fake-model"：整套里别的用例可能把 ★ 挪走，写死会在不相干的地方假红。
    base = client.get("/v1/models", headers=a).json()["default"]
    pid = _add(client, a)
    r = client.post("/v1/me/providers/default", json={"provider_id": pid}, headers=a)
    assert r.status_code == 200, r.text
    assert client.get("/v1/models", headers=a).json()["default"] == pid
    assert client.get("/v1/me/providers", headers=a).json()["default"] == pid

    # 用户也可以把默认押在共享条目上
    shared_id = client.get("/v1/models", headers=a).json()["models"][0]["id"]
    assert client.post("/v1/me/providers/default", json={"provider_id": shared_id}, headers=a).status_code == 200

    # 指回私有再删掉：默认回落全局
    client.post("/v1/me/providers/default", json={"provider_id": pid}, headers=a)
    assert client.delete(f"/v1/me/providers/{pid}", headers=a).status_code == 200
    assert client.get("/v1/models", headers=a).json()["default"] == base

    assert client.post("/v1/me/providers/default", json={"provider_id": "ghost-id"}, headers=a).status_code == 404


def test_my_default_can_point_at_shared_provider(client, enforced):
    a = enforced("shared-pref-user")
    r = client.post("/v1/me/providers/default", json={"provider_id": "fake-model"}, headers=a)
    assert r.status_code == 200, r.text
    assert client.get("/v1/models", headers=a).json()["default"] == "fake-model"


# ---------- 聊天入口：不带 provider 时用「我的默认」 ----------

def test_chat_without_provider_uses_my_default(client, enforced, monkeypatch):
    a = enforced("default-route-user")
    pid = _add(client, a)
    client.post("/v1/me/providers/default", json={"provider_id": pid}, headers=a)
    r = _chat(client, a)
    assert r.status_code == 200, r.text
    assert r.json()["provider"] == pid
