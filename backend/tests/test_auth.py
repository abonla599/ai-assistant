"""访问口令鉴权测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import app.main as main_module

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
