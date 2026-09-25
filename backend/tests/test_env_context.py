"""发给模型的那句「你运行在「AI 助手」应用内 … 服务端版本 …」。

为什么单独锁（与 test_today_context 同一形状的事故账）：用户在该应用里问模型
「这个软件现在版号是多少」，模型答不出来——它此前收不到任何关于自己所在环境的
事实，只能凭训练语料猜。日期锚点当年就是这么补上的，环境锚点欠的是同一笔账。

锁的三件事：
1. **口径**：版本号只认 `app.core.buildinfo.build_version()` 那一份构建戳
   （version.txt）。在这里或别处再写一个字面量 "v0.20"，就是本仓反复付过钱的
   "第二个事实来源"。
2. **兜底**：构建戳取不到时如实写"未登记"，绝不编一个号——宁缺毋假。
3. **形状与接线**：走 `_append_system` 进那一条 system、排在日期之后不动 persona；
   非流式 `process` 与流式端点两条路都要在实际交给模型的那份 messages 上验到。
"""
from types import SimpleNamespace

import pytest

from app import pipeline as pipeline_mod
from app.core import schedule
from app.pipeline import ChatPipeline

PERSONA = "你是考研规划助手，只用中文回答。"
DATE_LINE_OK = "环境锚点没进那一条 system"


def _sys_text(msgs):
    systems = [m for m in msgs if m["role"] == "system"]
    assert len(systems) == 1, f"只许有一条 system，实际 {msgs}"
    return systems[0]["content"]


def _fake_client(sink):
    def create(**kwargs):
        sink.append(kwargs)
        msg = SimpleNamespace(content="（测试回复）", tool_calls=None,
                              model_dump=lambda: {"role": "assistant",
                                                  "content": "（测试回复）"})
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


# ---------- 1. 口径与兜底 ----------

def test_env_line_quotes_the_build_stamp_as_the_single_source(monkeypatch):
    """版本号必须逐字来自 build_version()：mock 成什么，句子里就是什么。"""
    monkeypatch.setattr(pipeline_mod, "build_version", lambda: "v9.87")
    line = pipeline_mod._env_line()
    assert "v9.87" in line, f"构建戳没被引用：{line!r}"


def test_env_line_says_unregistered_instead_of_inventing_a_version(monkeypatch):
    """取不到构建戳时如实说"未登记"——不许把猜测或上一版的号发出去。"""
    monkeypatch.setattr(pipeline_mod, "build_version", lambda: "")
    line = pipeline_mod._env_line()
    assert "未登记" in line, f"没有兜底措辞，可能编了个号：{line!r}"


# ---------- 2. 形状：进同一条 system，排在日期后 ----------

def test_env_anchor_is_injected_bare_and_dressed(monkeypatch):
    monkeypatch.setattr(schedule, "today", lambda: "2026-09-23")
    monkeypatch.setattr(pipeline_mod, "build_version", lambda: "v3.14")

    msgs, _ = ChatPipeline("u_env_bare").inject_context(
        [{"role": "user", "content": "这个软件版本号多少"}], "这个软件版本号多少")
    assert [m["role"] for m in msgs] == ["system", "user"], msgs
    text = _sys_text(msgs)
    assert text.split("\n\n")[0] == "今天是 2026-09-23 周三。", \
        f"日期首段被挤掉了：{text!r}"
    assert "「AI 助手」应用内" in text and "v3.14" in text, f"{DATE_LINE_OK}：{text!r}"

    msgs, _ = ChatPipeline("u_env_dressed").inject_context(
        [{"role": "system", "content": PERSONA},
         {"role": "user", "content": "你住在哪个软件里"}], "你住在哪个软件里")
    assert [m["role"] for m in msgs] == ["system", "user"], msgs
    text = msgs[0]["content"]
    assert text.startswith(PERSONA), f"persona 被动过：{text!r}"
    assert "「AI 助手」应用内" in text[len(PERSONA):], f"环境锚点没追加在 persona 后：{text!r}"


# ---------- 3. 接线：两条路都在真发给模型的 messages 上验 ----------

def test_process_sends_the_env_anchor_to_the_model(monkeypatch):
    import app.pipeline as pm

    monkeypatch.setattr(schedule, "today", lambda: "2026-09-23")
    monkeypatch.setattr(pm, "build_version", lambda: "v2.71")
    sent = []
    monkeypatch.setattr(pm, "build_client", lambda provider: _fake_client(sent))

    result = ChatPipeline("u_env_process").process(
        "fake-chat", [{"role": "user", "content": "这个软件什么版本"}],
        provider_id="fake-model")

    assert result["reply"] == "（测试回复）", result
    assert len(sent) == 1, f"模型调用次数不对：{sent}"
    text = _sys_text(sent[0]["messages"])
    assert "v2.71" in text, f"非流式这条没带上环境锚点：{text!r}"


def test_stream_endpoint_sends_the_env_anchor_to_the_model(client, monkeypatch):
    from app.core import streaming

    monkeypatch.setattr(schedule, "today", lambda: "2026-09-23")
    monkeypatch.setattr(pipeline_mod, "build_version", lambda: "v1.35")
    seen = []

    def spy(model, messages, provider_id=None, temperature=0.7, max_tokens=4096,
            tools=None, max_tool_turns=5, user_id=None):
        seen.append([dict(m) for m in messages])
        yield "好"

    monkeypatch.setattr(streaming, "stream_chat", spy)

    r = client.post("/v1/chat/stream", json={
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "这个软件版本号多少"}]})
    assert r.status_code == 200, r.text
    assert len(seen) == 1, seen
    text = _sys_text(seen[0])
    assert "v1.35" in text, f"流式这条没带上环境锚点：{text!r}"
    assert text.split("\n\n")[0].startswith("今天是 "), \
        f"日期首段被挤掉：{text!r}"
