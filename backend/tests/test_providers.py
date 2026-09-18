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
    """路由依赖树里的可调用对象（含子依赖）——遍历本身用契约测试那一份。

    这里曾经是这套 walker 的第四份拷贝，而且**已经分叉**：它不走
    `dependant.websocket`，而 test_route_auth_contract._callables_of 走。两份
    walker 的差异意味着"契约测试说这条覆盖了"与"本文件说这条覆盖了"根本不是同一
    件事——挂在 websocket 依赖上的管理员守卫，本文件这条就会看不见。既然要的是
    "按真实路由表扫描"，遍历规则就必须与契约测试同源，否则这把锁自己就是盲区。
    """
    from tests.test_route_auth_contract import _callables_of

    found = set(_callables_of(route.dependant))
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


# ---------- 信任锚：本机有 HTTPS 中间人时不许整条链路降级 ----------

def _app_source_files():
    import app as app_pkg
    root = Path(app_pkg.__file__).parent
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def test_no_openai_client_is_built_without_an_explicit_http_client():
    """每一处 OpenAI(...) 都必须自带 http_client。

    本机卡巴斯基在拆 TLS：它签发的证书在 certifi 那份根证书包里，所以默认构造的
    客户端一律 CERTIFICATE_VERIFY_FAILED。漏一处的后果还不一样——聊天会红着脸报错，
    而 memory_manager 那处会静默降级成伪嵌入，语义检索再也读不到记忆，界面上毫无痕迹。
    """
    import re

    offenders = []
    for path in _app_source_files():
        text = path.read_text(encoding="utf-8")
        for call in re.finditer(r"OpenAI\(([^)]*)\)", text, re.S):
            if "http_client" not in call.group(1):
                offenders.append(path.name)
    assert not offenders, f"以下文件里的 OpenAI 客户端没带 http_client：{sorted(set(offenders))}"


def test_the_trust_anchor_still_verifies_certificates():
    """换信任锚不等于关校验：证书必须照验、主机名必须对照。"""
    import ssl

    from app.core.tls import system_ssl_context

    ctx = system_ssl_context()
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED, "不许把校验关掉"
    assert ctx.check_hostname, "不许关掉主机名比对"


def test_build_client_feeds_that_context_to_httpx(monkeypatch):
    """build_client 必须真的把上下文交给 httpx，而不是攒着不用。

    用抛哨兵异常的方式截获构造参数：假客户端要装到能让 openai SDK 跑完，就得连
    timeout、base_url 一起仿，那测的就成了 SDK 的内部约定而不是我们的接线。
    """
    import pytest

    from app.core import providers

    seen = {}

    class Recorder(Exception):
        pass

    class FakeClient:
        def __init__(self, **kwargs):
            seen.update(kwargs)
            raise Recorder()

    monkeypatch.setattr(providers.httpx, "Client", FakeClient)
    # 打在 providers 的名字上：它是 from ... import 进来的直接绑定，
    # 改 tls 模块那份属性对 build_client 不起作用
    monkeypatch.setattr(providers, "system_ssl_context", lambda: "SENTINEL")
    with pytest.raises(Recorder):
        providers.build_client({"api_key": "sk-x", "base_url": "https://api.deepseek.com"})
    assert seen.get("verify") == "SENTINEL", f"httpx 没拿到系统信任锚：{seen}"
