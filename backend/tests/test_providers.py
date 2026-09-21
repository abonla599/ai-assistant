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


# ---------- 「默认用哪个」只有一份答案 ----------
# 服务端 ProviderStore.default() 原先只看 is_default 标记，前端那份
# `usable.find(p => p.default) || usable[0]` 却先看密钥可用性。可用性判据两边都现成
# （providers.looks_placeholder，catalog() 用它算 usable），所以漂移不是"两种定义"，
# 而是服务端漏用了一处：管理员给一个填了占位符密钥的 provider 点了 ★，界面就显示
# 「实际会用 B」而 resolve() 用的是 A（随后要么报错，要么拿 A 的配置去打上游）。

def _store_at(tmp_path, items):
    """从一份现成的 providers.json 起独立 store，不碰 conftest 那个进程级单例。"""
    import json

    from app.core.providers import ProviderStore

    path = tmp_path / "providers.json"
    path.write_text(json.dumps(items), encoding="utf-8")
    return ProviderStore(path=str(path))


UNUSABLE_DEFAULT = {
    "id": "dead-default", "label": "占位默认", "base_url": "https://a.invalid/v1",
    "api_key": "your-key-here", "model": "gpt-4o", "supports_vision": False,
    "is_default": True,
}
USABLE_OTHER = {
    "id": "live-other", "label": "真能用", "base_url": "https://b.invalid/v1",
    "api_key": "sk-real-looking-key-123456", "model": "deepseek-chat",
    "supports_vision": False, "is_default": False,
}


def test_default_only_picks_among_usable_providers(tmp_path):
    """default() 与前端同口径：先在有有效密钥的里面挑，才谈得上"默认"。"""
    from app.core.providers import ProviderError

    store = _store_at(tmp_path, [UNUSABLE_DEFAULT, USABLE_OTHER])
    assert store.default()["id"] == "live-other"
    try:
        resolved = store.resolve()
    except ProviderError as e:                      # 修好之前这里就抛"缺少有效密钥"
        raise AssertionError(f"resolve() 仍落到不可用的默认：{e}")
    assert resolved["id"] == "live-other"


def test_resolve_still_names_the_missing_key_when_nothing_is_usable(tmp_path):
    """一个都不可用时不许改成"尚未配置任何模型服务"——那是另一件事，会让人去重配。"""
    import pytest

    from app.core.providers import ProviderError

    store = _store_at(tmp_path, [UNUSABLE_DEFAULT,
                                 {**USABLE_OTHER, "api_key": "填入你的密钥"}])
    with pytest.raises(ProviderError) as exc:
        store.resolve()
    assert "密钥" in str(exc.value)


def test_models_endpoint_default_skips_unusable_provider():
    """接口报给前端的 default 必须是它真正会用的那一个。"""
    saved = client.post("/v1/providers", json=_payload(api_key="your-key-here")
                        ).json()["provider"]
    try:
        # 走「设为默认」而不是 POST 时带 is_default：后者那条分支不清别人的标记，
        # 库里能同时躺着两颗 ★，那就不是在测漂移了（那是另一件事）。
        assert client.post(f"/v1/providers/{saved['id']}/default").status_code == 200
        data = client.get("/v1/models").json()
        assert data["default"] == "fake-model", \
            f"/v1/models 把默认报成了不可用的 {data['default']}"
        starred = next(m for m in data["models"] if m["id"] == saved["id"])
        assert starred["default"] is True and starred["usable"] is False, \
            "★ 标记与「实际会用」是两件事，清单里得同时看得见"
    finally:
        client.delete(f"/v1/providers/{saved['id']}")


def test_providers_never_leak_plaintext_key():
    saved = client.post("/v1/providers", json=_payload()).json()["provider"]
    body = client.get("/v1/providers").text
    assert "sk-real-looking-key-123456" not in body
    # 这两行原本钉的是 `startswith("sk-")` 与"里面有省略号"——那正是被改掉的行为：
    # 前缀认得出厂商、长度框得下爆破面，掩码自己就成了第二个泄露点。现在只许末四位。
    assert saved["api_key_masked"].endswith("3456"), saved["api_key_masked"]
    assert "sk-" not in saved["api_key_masked"] and "123456" not in saved["api_key_masked"]


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


def test_adding_a_default_provider_clears_the_previous_star():
    """两颗 ★ 让"默认是哪个"重新变成两个答案，而 default() 只认遍历到的第一颗。

    更新路径会清别人的标记，新增路径不会——这是同一个不变式只写了一半。
    """
    before = client.get("/v1/models").json()["models"]
    start = [m["id"] for m in before if m["default"]]
    assert len(start) == 1, f"起点就该只有一颗 ★，实际 {start}"

    added = client.post("/v1/providers", json=_payload(is_default=True)).json()["provider"]
    after = client.get("/v1/models").json()["models"]
    stars = [m["id"] for m in after if m["default"]]
    assert len(stars) == 1, f"新增带 is_default 的 provider 后剩 {len(stars)} 颗 ★：{stars}"
    assert stars == [added["id"]]


def test_seeded_deepseek_is_the_vision_capable_one(monkeypatch):
    """首次播种那条必须真能吃图，且 PRESETS 与它口径一致。

    2026-09-20 实测：api.deepseek.com 的 /v1/models 只列 deepseek-flash 与
    deepseek-v4-pro。拿真截图打 deepseek-flash，prompt_tokens 计入了图像并读出
    图中文字；打 deepseek-v4-pro 它回"我无法查看这张图片"。播种时把
    supports_vision 抄成 False，新机器上传第一张图就只会得到一句"不支持图片输入"
    ——界面显示的是 DeepSeek，实际能力却被自己的配置锁住了。
    """
    from app.core import providers

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-real-looking-key-123456")
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    seeded = providers._seed_from_env()
    assert len(seeded) == 1
    rec = seeded[0]
    assert rec["model"] == "deepseek-flash"
    assert rec["label"] == "deepseek-flash", "设置页「模型名」这栏显示的就是它，别写对不上的花名"
    assert rec["supports_vision"] is True
    preset = providers.PRESETS["deepseek"]
    assert (preset["model"], preset["supports_vision"]) == \
        (rec["model"], rec["supports_vision"]), "同一个厂商留了两份答案"


def test_placeholder_deepseek_key_seeds_nothing(monkeypatch):
    """正对照：上面那条不是恒真——密钥是占位符时一条都不播种。"""
    from app.core import providers

    monkeypatch.setenv("DEEPSEEK_API_KEY", "your-key-here")
    assert providers._seed_from_env() == []


# ---------- 密钥离开记录，只活在旁边那个文件里 ----------
# 2026-09-21 的现状：providers.json 是一行 `"api_key": "sk-…"` 的明文 JSON，而
# mask_key 会把前 3 位与总长度一起给出去。"用户自带 key" 这条路线要求服务端
# 尽量没有可泄露的东西，所以先把管理员这把挪出记录本身：记录里只留引用，
# 值放在同目录的 provider_keys.json。文件与目录仍由 PROVIDERS_DB_PATH 一个变量
# 指走（密钥文件从记录路径推导，不再新开一个环境变量——两处事实来源会漂移）。

STORED_KEY = "sk-adminkey-99887766554433"


def _fresh_store(tmp_path):
    from app.core.providers import ProviderStore
    return ProviderStore(path=str(tmp_path / "providers.json"))


def test_mask_key_gives_nothing_but_the_last_four():
    """掩码是**唯一**给前端的凭据线索，所以它自己不许成为第二个泄露点。"""
    from app.core.providers import mask_key
    masked = mask_key(STORED_KEY)
    assert masked.endswith(STORED_KEY[-4:]), masked
    assert STORED_KEY[:3] not in masked, "前缀还在：sk-/pk- 这类前缀 + 长度足够缩小爆破面"
    assert str(len(STORED_KEY)) not in masked, "总长度也是信息"


def test_the_record_on_disk_has_no_key_at_all(tmp_path):
    saved = _fresh_store(tmp_path).upsert(
        {"label": "管理员 DeepSeek", "base_url": "https://api.deepseek.com/v1",
         "api_key": STORED_KEY, "model": "deepseek-flash"})
    raw = (tmp_path / "providers.json").read_text(encoding="utf-8")
    assert STORED_KEY not in raw, "key 还写在记录里"
    assert "api_key" not in raw, "留一个空字段也行不通：读它的人会以为值在这儿"
    assert saved["id"] in (tmp_path / "provider_keys.json").read_text(encoding="utf-8")


def test_the_key_still_resolves_after_a_reload(tmp_path):
    """搬走不等于搬丢——重启后还能取回来，否则这就是个"效果没了但不报错"。"""
    saved = _fresh_store(tmp_path).upsert(
        {"label": "A", "base_url": "https://a.invalid/v1", "api_key": STORED_KEY, "model": "m"})
    reloaded = _fresh_store(tmp_path)
    assert reloaded.resolve(saved["id"])["api_key"] == STORED_KEY


def test_a_legacy_inline_key_is_moved_out_and_still_usable(tmp_path):
    """现网那份 providers.json 里躺着一条内联 key：升级必须自己把它搬走。"""
    import json
    path = tmp_path / "providers.json"
    path.write_text(json.dumps([{
        "id": "deepseek-chat", "label": "deepseek-flash",
        "base_url": "https://api.deepseek.com/v1", "api_key": STORED_KEY,
        "model": "deepseek-flash", "supports_vision": True, "is_default": True,
    }]), encoding="utf-8")

    store = _fresh_store(tmp_path)
    assert store.resolve("deepseek-chat")["api_key"] == STORED_KEY
    assert STORED_KEY not in path.read_text(encoding="utf-8"), "读过了但没搬走"


def test_deleting_a_provider_takes_its_key_with_it(tmp_path):
    store = _fresh_store(tmp_path)
    saved = store.upsert({"label": "A", "base_url": "https://a.invalid/v1",
                          "api_key": STORED_KEY, "model": "m"})
    assert store.delete(saved["id"]) is True
    assert STORED_KEY not in (tmp_path / "provider_keys.json").read_text(encoding="utf-8"), \
        "记录删了、密钥还孤零零躺在盘上"


# ---------- 出口脱敏：异常文本会同时进日志和响应体 ----------

def test_scrub_hides_key_shaped_text():
    from app.core.providers import scrub_secrets
    text = ("Error code: 401 - invalid api key sk-abcdefghijklmnop\n"
            "request headers: {'authorization': 'Bearer zz-9876abcdef'}")
    cleaned = scrub_secrets(text)
    assert "sk-abcdefghijklmnop" not in cleaned and "zz-9876abcdef" not in cleaned
    assert "401" in cleaned, "只许隐去凭据，不许把定位线索一起抹了"


def test_scrub_hides_a_configured_key_whatever_shape_it_has(client):
    """没有 `sk-` 前缀时靠形状认不出来——但服务端知道**自己配过哪些值**。"""
    from app.core.providers import scrub_secrets
    odd = "Zht!9-nd_qe-Lm-0x"
    client.post("/v1/providers", json=_payload(api_key=odd))
    assert odd not in scrub_secrets(f"上游把凭据原样打印回来了：{odd}")


def test_fail_reason_scrubs_on_the_way_out():
    """`_fail_reason` 是日志与响应体共用的那一个出口，脱敏必须长在它身上。"""
    from app.main import _fail_reason
    boom = RuntimeError(f"bad key {STORED_KEY}")
    boom.__cause__ = ValueError(f"Bearer {STORED_KEY} rejected")
    out = _fail_reason(boom)
    assert STORED_KEY not in out
    assert "RuntimeError" in out and "ValueError" in out, "类型名是这行日志的全部价值"


def test_ping_does_not_echo_the_key(tmp_path, monkeypatch):
    """探活失败那句是给管理员看的，也是这份文件的第三处出口。"""
    import app.core.providers as providers
    store = _fresh_store(tmp_path)
    saved = store.upsert({"label": "A", "base_url": "https://a.invalid/v1",
                          "api_key": STORED_KEY, "model": "m"})

    # 打的是 OpenAI 这个名字本身：ping 现在是自己现构客户端的，
    # 拿 build_client 去打桩会桩到一个没人调的函数上，测试就永远假绿。
    class Exploding:
        class chat:
            class completions:
                @staticmethod
                def create(*a, **kw):
                    raise ValueError(f"upstream echoed credential {STORED_KEY} back to us")

    monkeypatch.setattr(providers, "OpenAI", lambda *a, **kw: Exploding)
    result = store.ping(saved["id"])
    assert result["ok"] is False
    assert STORED_KEY not in result["detail"], result["detail"]


def test_a_vendor_that_echoes_the_header_back_leaves_no_key_in_the_ping_detail(tmp_path):
    """真故障注入，不是正则单测。

    前面几条验的是"正则认得出 sk-"，这一条验的是那条真正会伤人的路径：网关把
    收到的 Authorization 原样回在 401 正文里（这类中转站的常规做法），异常文本
    会带着它一路走到管理员屏幕与 data/backend.log。打的是本机回环，不花钱、不出网。
    """
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Echo401(BaseHTTPRequestHandler):
        def do_POST(self):
            auth = self.headers.get("authorization", "")
            payload = f'{{"error":{{"message":"invalid key, you sent {auth}"}}}}'.encode()
            self.send_response(401)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Echo401)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        store = _fresh_store(tmp_path)
        saved = store.upsert({"label": "回显网关", "model": "m",
                              "base_url": f"http://127.0.0.1:{srv.server_port}/v1",
                              "api_key": STORED_KEY})
        result = store.ping(saved["id"])
    finally:
        srv.shutdown()
        srv.server_close()

    assert result["ok"] is False, result
    assert STORED_KEY not in result["detail"], f"密钥从探活结果里漏出去了：{result['detail']}"
    assert "401" in result["detail"], "脱敏不该把「上游回了 401」这条线索一起抹掉"
