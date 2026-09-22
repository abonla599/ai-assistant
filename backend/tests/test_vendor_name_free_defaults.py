"""F-1f 红线守卫：服务商名不得刻进被跟踪源码的默认值。

口径（与项目管理员 20260922 评审一致）：模型选择一律"默认 None → 现场走
setDefault 的 provider"。providers.py 里的 PRESETS/环境种子是配置的合法落点，
注释里为解释红线而提到的名字也不算刻名——真正要拦的是**功能性默认值**：
它会在客户端不传模型时替用户做主，把某个服务商焊死在代码路径里。

这里用签名内省而不是源码 grep 做守卫：grep 分不清"注释里讲历史"与"参数里
刻名字"，内省只认前者会漏、后者会误伤的那条界线恰好是默认值本身。
若有人把 model="deepseek-chat" 加回任何一条入口的默认位，本文件必红。
"""
import inspect

import pytest


def _default_of(func, param: str):
    sig = inspect.signature(func)
    assert param in sig.parameters, f"{func.__qualname__} 签名里不再有 {param}"
    return sig.parameters[param].default


@pytest.mark.parametrize("cls", [
    "app.agents.planner.Planner",
    "app.agents.executor.Executor",
    "app.agents.orchestrator.Orchestrator",
    "app.agents.react_agent.ReActAgent",
])
def test_agent_ctors_default_model_to_none(cls):
    module, _, name = cls.rpartition(".")
    klass = getattr(__import__(module, fromlist=[name]), name)
    assert _default_of(klass.__init__, "model") is None, (
        f"{cls} 的 model 默认值又不是 None 了：服务商名不许刻回源码")


def test_http_entrypoints_default_model_to_none():
    from app.main import AgentRequest, ChatRequest, create_session
    assert ChatRequest.model_fields["model"].default is None
    assert AgentRequest.model_fields["model"].default is None
    assert _default_of(create_session, "model") is None


def test_none_really_resolves_to_the_default_provider():
    """None 不是"报错"也不是"随便哪个"：resolve 对空名字的兜底就是默认那颗。

    守卫测试只证明默认值是 None；这条证明 None 这条路**通**到 setDefault，
    两半合起来才是"留空=用户设置页里那颗"的完整契约。
    """
    from app.core.providers import store as provider_store
    provider = provider_store.resolve(None, legacy_model=None)
    assert provider.get("is_default")


def test_omitted_chat_model_reaches_the_default_provider_end_to_end():
    """客户端整个不传 model 字段时，/v1/chat 必须正常出话（而不是 422/400）。

    ChatRequest 的默认值从字符串改成 Optional 是可空性变化，pydantic 层就有人
    写错；这条用最便宜的方式钉住"不传也行、且走默认"。conftest 已把 LLM 调用
    整体打桩，这里断言的是路由装配而非真实网络。
    """
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    res = client.post("/v1/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert res.status_code == 200, res.text
    body = res.json()
    assert "reply" in body
    # 应答里回的 provider/model 来自 resolve，不是请求里刻的名字
    assert body.get("provider") and body.get("model")


def test_session_without_model_stays_a_string_field():
    """create_session 不传 model 时存 ""，而不是把形状改成 null。

    老客户端的列表渲染直接贴 session["model"]；None 会渲染成字面 "null"。
    显式传旧别名的路径（test_integration 里那条）不受影响。
    """
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)   # conftest 下 AUTH_MODE=disabled，无需凭据
    sid = client.post("/v1/sessions").json()["session_id"]
    sess = client.get(f"/v1/sessions/{sid}").json()
    assert isinstance(sess["model"], str)
