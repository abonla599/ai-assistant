"""模型服务（Provider）配置与派生模型清单测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def _payload(**over):
    base = {"label": "Qwen 视觉", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "sk-real-looking-key-123456", "model": "qwen-vl-max",
            "supports_vision": True, "is_default": False}
    base.update(over)
    return base


def test_models_endpoint_is_derived_from_providers():
    data = client.get("/v1/models").json()
    ids = [m["id"] for m in data["models"]]
    assert "fake-model" in ids, f"派生清单缺少已配置的 provider: {ids}"
    entry = next(m for m in data["models"] if m["id"] == "fake-model")
    assert entry["usable"] is True
    assert entry["name"] == "测试模型"


def test_placeholder_key_marks_model_unusable():
    """非空但形如占位符的密钥必须判为不可用——这正是"切换模型没反应"的根因。"""
    saved = client.post("/v1/providers", json=_payload(api_key="your-key-here")).json()["provider"]
    models = client.get("/v1/models").json()["models"]
    entry = next(m for m in models if m["id"] == saved["id"])
    assert entry["usable"] is False
    assert "密钥" in entry["reason"]


def test_providers_never_leak_plaintext_key():
    saved = client.post("/v1/providers", json=_payload()).json()["provider"]
    body = client.get("/v1/providers").text
    assert "sk-real-looking-key-123456" not in body
    assert saved["api_key_masked"].startswith("sk-")
    assert "…" in saved["api_key_masked"]


def test_update_without_key_keeps_existing():
    """编辑界面回显的是掩码，若把掩码存回去密钥就废了。"""
    saved = client.post("/v1/providers", json=_payload()).json()["provider"]
    updated = client.put(f"/v1/providers/{saved['id']}",
                         json={**_payload(), "api_key": "", "label": "改名了"}).json()["provider"]
    assert updated["label"] == "改名了"
    assert updated["has_key"] is True


def test_invalid_base_url_rejected():
    res = client.post("/v1/providers", json=_payload(base_url="ftp://bad"))
    assert res.status_code == 400
    assert "base_url" in res.json()["detail"]


def test_set_default_and_delete():
    saved = client.post("/v1/providers", json=_payload()).json()["provider"]
    assert client.post(f"/v1/providers/{saved['id']}/default").status_code == 200
    default = next(p for p in client.get("/v1/providers").json()["providers"] if p["is_default"])
    assert default["id"] == saved["id"]

    assert client.delete(f"/v1/providers/{saved['id']}").status_code == 200
    assert client.delete(f"/v1/providers/{saved['id']}").status_code == 404
    # 删掉默认项后必须仍有默认，否则聊天会无模型可用
    assert any(p["is_default"] for p in client.get("/v1/providers").json()["providers"])


def test_unknown_model_falls_back_to_default_and_reports_real_model():
    """界面必须知道实际是哪个模型在答，避免"显示 A 实际 B"。"""
    sid = client.post("/v1/sessions").json()["session_id"]
    res = client.post("/v1/chat", json={
        "model": "根本不存在-的模型", "messages": [{"role": "user", "content": "hi"}],
        "session_id": sid})
    assert res.status_code == 200
    body = res.json()
    assert body["model"] == "fake-chat"
    assert body["provider"] == "fake-model"


def test_provider_draft_test_rejects_placeholder_key():
    res = client.post("/v1/providers/test", json=_payload(api_key="your-key-here"))
    assert res.status_code == 200
    assert res.json()["ok"] is False
    assert "API Key" in res.json()["detail"]


def _guard_callables(route) -> set:
    """路由依赖树里的可调用对象（含子依赖）。"""
    found, stack = set(), [route.dependant]
    while stack:
        cur = stack.pop()
        for sub in cur.dependencies:
            found.add(sub.call)
            stack.append(sub)
    for dep in getattr(route, "dependencies", ()) or ():
        if getattr(dep, "dependency", None) is not None:
            found.add(dep.dependency)
    return found


def test_every_provider_route_is_admin_gated():
    """路由表一侧：/v1/providers 之下每一条都必须挂 require_admin。

    test_isolation 那 7 条断的是"普通用户拿到 403"，但它只看得到自己列出的那
    几条路由：新加一条 `@app.post("/v1/providers/import")` 而只挂 CurrentPrincipal，
    那里一条都不会红。这一条按真实路由表扫描，覆盖面由路径前缀决定而不是由清单决定。
    """
    from app.core.authz import require_admin

    routes = [r for r in app.routes
              if getattr(r, "path", "").startswith("/v1/providers")
              and getattr(r, "dependant", None) is not None]
    assert len(routes) >= 5, f"扫描本身大概坏了，只看到 {len(routes)} 条 providers 路由"
    bare = [f"{sorted(r.methods - {'HEAD', 'OPTIONS'})} {r.path}" for r in routes
            if require_admin not in _guard_callables(r)]
    assert not bare, f"以下模型服务路由允许普通用户进入：{bare}"
