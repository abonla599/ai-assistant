"""访问口令鉴权测试（HTTP 层）+ 身份存储单元测试（纯存储层，不涉及 HTTP）。"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.auth import AuthError, AuthStore, hash_token

TOKEN = "test-token-abc123"


@pytest.fixture
def guarded(monkeypatch):
    """开启口令后的客户端。"""
    monkeypatch.setattr(main_module, "ACCESS_TOKEN", TOKEN)
    return TestClient(main_module.app)


@pytest.fixture
def open_client(monkeypatch):
    """未配置口令时的客户端（本机开发默认）。"""
    monkeypatch.setattr(main_module, "ACCESS_TOKEN", "")
    return TestClient(main_module.app)


def test_open_when_no_token_configured(open_client):
    assert open_client.get("/v1/models").status_code == 200


def test_api_requires_token(guarded):
    assert guarded.get("/v1/models").status_code == 401


def test_wrong_token_rejected(guarded):
    res = guarded.get("/v1/models", headers={"Authorization": "***"})
    assert res.status_code == 401


def test_bearer_token_accepted(guarded):
    res = guarded.get("/v1/models", headers={"Authorization": f"Bearer {TOKEN}"})
    assert res.status_code == 200


def test_custom_header_accepted(guarded):
    assert guarded.get("/v1/models", headers={"X-Access-Token": TOKEN}).status_code == 200


def test_scheme_is_case_insensitive(guarded):
    """RFC 7235：认证方案名大小写不敏感"""
    for scheme in ("Bearer", "bearer", "BEARER"):
        res = guarded.get("/v1/models", headers={"Authorization": f"{scheme} {TOKEN}"})
        assert res.status_code == 200, scheme


def test_bare_token_without_scheme_accepted(guarded):
    assert guarded.get("/v1/models", headers={"Authorization": TOKEN}).status_code == 200


def test_unknown_scheme_rejected(guarded):
    res = guarded.get("/v1/models", headers={"Authorization": f"Basic {TOKEN}"})
    assert res.status_code == 401


def test_stream_endpoint_also_guarded(guarded):
    res = guarded.post("/v1/chat/stream", json={
        "model": "deepseek-chat", "messages": [{"role": "user", "content": "hi"}]})
    assert res.status_code == 401


def test_pwa_shell_stays_public(guarded):
    """页面本身不含密钥，可公开；否则用户无法进入设置页填口令。"""
    assert guarded.get("/app/").status_code == 200
    assert guarded.get("/health").status_code == 200


def test_api_docs_are_guarded(guarded):
    """接口文档会暴露全部端点，对外不应免鉴权。"""
    assert guarded.get("/docs").status_code == 401
    assert guarded.get("/openapi.json").status_code == 401


# ---------- 身份存储 app/core/auth.py（纯存储层，不涉及 HTTP） ----------


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


def test_flush_survives_reload(tmp_path):
    """写盘必须真的可回读：原子替换没生效时这条会红。"""
    path = str(tmp_path / "users.json")
    store = AuthStore(path=path, invites_path=str(tmp_path / "invites.json"))
    code = store.create_invite("admin")
    principal, _ = store.register(code=code, username="周八")
    reloaded = AuthStore(path=path, invites_path=str(tmp_path / "invites.json"))
    assert [u["user_id"] for u in reloaded.list_users()] == [principal.user_id]
    assert not os.path.exists(path + ".tmp"), "临时文件必须被原子替换掉"
