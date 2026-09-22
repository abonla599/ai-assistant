"""对话上下文里那句「今天是 …」。

为什么单独锁这一行：2026-09-22 的真事故是运营者在 app 里问「2027 年研究生简章」
搜不到、问「今天几号」却答得出来——模型从头到尾没收到过服务端日期，于是"明年"
没有锚点、也无从判断该不该去搜。所以这里锁的不是"消息里有个日期"（那是空锁），
而是三件更容易做错的事：

1. **口径**：只认 `app.core.schedule.today()` 那一份"今天"——提醒功能用的就是它，
   第二套定义迟早会在跨零点时给出两个不同的"今天"；星期几同样不许写死。
2. **形状**：走 `ChatPipeline._append_system` 进**那一条** system。前端
   （`app/web/static/app.js:178`）会带着用户自己的 persona 当 `messages[0]`，
   所以既不能插出第二条 system，也不能把它盖掉或换到它前面。
3. **两条路**：非流式 `process` 与流式 `/v1/chat/stream` 都要在**实际发给模型的
   那份 messages** 上验到，只测 `inject_context` 本身等于什么都没接上。
"""
import re
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.core import schedule
from app.pipeline import ChatPipeline

# 前端 persona 的形状（app/web/static/app.js:178 把它 unshift 成 messages[0]）。
PERSONA = "你是考研规划助手，只用中文回答。"


def _fake_client(sink):
    """假 OpenAI 客户端：把每次 create 收到的 kwargs 记下来，回复固定。

    形状要照真的来（`client.chat.completions.create`、`choices[0].message`、
    `usage`），否则锁验的是夹具而不是接线。
    """

    def create(**kwargs):
        sink.append(kwargs)
        msg = SimpleNamespace(content="（测试回复）", tool_calls=None,
                              model_dump=lambda: {"role": "assistant",
                                                  "content": "（测试回复）"})
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


# ---------- 1. 口径：今天 = schedule.today()，星期几算出来 ----------

@pytest.mark.parametrize("day,weekday", [
    ("2026-09-22", "周二"),   # 事故发生那天
    ("2027-01-01", "周五"),   # 跨年：故意和上面不同星期几
    ("2026-12-31", "周四"),   # 年末：又一个不同星期几
])
def test_injected_context_carries_the_authoritative_today(monkeypatch, day, weekday):
    """「今天」必须取自 schedule.today()，星期几必须跟着它算。

    这里 mock 的是 `schedule.today` 本身：实现只要绕过它自己 `datetime.now()`，
    mock 就落不到实处，断言当场红——这正是"不许引入第二套今天"的可执行版本。
    三个不同星期几的参数则盯着"写死周一"这类改法。
    """
    monkeypatch.setattr(schedule, "today", lambda: day)

    msgs, _ = ChatPipeline("u_today_unit").inject_context(
        [{"role": "user", "content": "2027 年研究生简章出了吗"}], "2027 年研究生简章出了吗")

    systems = [m for m in msgs if m["role"] == "system"]
    assert len(systems) == 1, f"只许有一条 system，实际 {msgs}"
    assert [m["role"] for m in msgs] == ["system", "user"], \
        f"日期那条必须是首条 system，不该挪动消息顺序或多插一条：{msgs}"
    # 日期排在被注入内容的最前面：记忆/偏好有没有命中都不该影响它（无命中时它就是整条）
    assert systems[0]["content"].split("\n\n")[0] == f"今天是 {day} {weekday}。", \
        systems[0]["content"]


def test_the_unmocked_today_agrees_with_the_reminder_feature(monkeypatch):
    """不 mock 的对照：证明上面那条不是"把 mock 的值当真"。

    把日期写死成某一天，除了那天之外每天都红；换成另一个时钟（UTC、浏览器传来的
    日期、本地 naive）也会在这里红——因为对表的是提醒功能用的那一份 `schedule.today()`。
    """
    before = schedule.today()
    msgs, _ = ChatPipeline("u_today_real").inject_context(
        [{"role": "user", "content": "今天几号"}], "今天几号")
    after = schedule.today()   # 跨零点时允许这两天，别的都不许

    line = msgs[0]["content"].split("\n\n")[0]
    found = re.fullmatch(r"今天是 (\d{4}-\d{2}-\d{2}) (周[一二三四五六日])。", line)
    assert found, f"日期那一句形状不对：{line!r}"
    assert found.group(1) in {before, after}, f"注入了 {found.group(1)}，服务端今天是 {before}"
    # 星期几和它旁边的日期自洽（真算出来的，不是抄的、更不是写死的）
    weekday = schedule._WEEKDAYS[datetime.strptime(found.group(1), "%Y-%m-%d").weekday()]
    assert found.group(2) == weekday, f"{found.group(1)} 是 {weekday}，却写成了 {found.group(2)}"


# ---------- 2. 形状：进那一条 system，不动前端带来的 persona ----------

def test_the_date_goes_into_the_existing_system_message(monkeypatch):
    """三条错法都要在这里红：插第二条 system、覆盖掉 persona、把日期排到 persona 前面。"""
    monkeypatch.setattr(schedule, "today", lambda: "2026-09-22")

    msgs, _ = ChatPipeline("u_today_shape").inject_context(
        [{"role": "system", "content": PERSONA},
         {"role": "user", "content": "你好"},
         {"role": "assistant", "content": "在的"},
         {"role": "user", "content": "2027 年简章"}], "2027 年简章")

    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"], \
        f"消息条数或顺序被动过：{msgs}"
    text = msgs[0]["content"]
    assert text.startswith(PERSONA), f"persona 被覆盖或挪走了：{text!r}"
    assert "今天是 2026-09-22 周二。" in text[len(PERSONA):], \
        f"日期没接在 persona 之后：{text!r}"


# ---------- 3. 接线：两条路各自发给模型的那份 messages ----------

def test_process_sends_the_date_to_the_model(monkeypatch):
    """非流式：`inject_context` 自己对不算完，要看真正交给 create() 的那一份。"""
    import app.pipeline as pipeline_mod

    monkeypatch.setattr(schedule, "today", lambda: "2027-03-05")
    sent = []
    monkeypatch.setattr(pipeline_mod, "build_client", lambda provider: _fake_client(sent))

    result = ChatPipeline("u_today_process").process(
        "fake-chat", [{"role": "user", "content": "2028 年研究生简章出了吗"}],
        provider_id="fake-model")

    assert result["reply"] == "（测试回复）", result
    assert len(sent) == 1, f"模型调用次数不对：{sent}"
    msgs = sent[0]["messages"]
    systems = [m for m in msgs if m["role"] == "system"]
    assert len(systems) == 1, f"发给模型的 messages 里有 {len(systems)} 条 system：{msgs}"
    assert systems[0]["content"].split("\n\n")[0] == "今天是 2027-03-05 周五。", \
        systems[0]["content"]
    assert [m["role"] for m in msgs] == ["system", "user"], f"原始消息被改动：{msgs}"


def test_stream_endpoint_sends_the_date_to_the_model(client, monkeypatch):
    """流式：`main.py` 那条 inject_context 一旦断开，这里立刻红。

    两条都发一遍：裸 user 消息（日期该是整条 system）、带 persona 的（日期只能追加，
    不许多出第二条 system）。
    """
    from app.core import streaming

    monkeypatch.setattr(schedule, "today", lambda: "2026-09-22")
    seen = []

    def spy(model, messages, provider_id=None, temperature=0.7, max_tokens=4096,
            tools=None, max_tool_turns=5, user_id=None):
        seen.append([dict(m) for m in messages])
        yield "好"

    monkeypatch.setattr(streaming, "stream_chat", spy)

    plain = client.post("/v1/chat/stream", json={
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "2027 年研究生简章"}]})
    assert plain.status_code == 200, plain.text

    with_persona = client.post("/v1/chat/stream", json={
        "model": "deepseek-chat",
        "messages": [{"role": "system", "content": PERSONA},
                     {"role": "user", "content": "2027 年研究生简章"}]})
    assert with_persona.status_code == 200, with_persona.text

    assert len(seen) == 2, f"流式没把请求接到模型上：{seen}"

    bare, dressed = seen
    assert [m["role"] for m in bare] == ["system", "user"], bare
    # 只认第一段：这条走的是默认身份，别的用例给他攒下过偏好摘要，那不该算在这里头上
    assert bare[0]["content"].split("\n\n")[0] == "今天是 2026-09-22 周二。", bare[0]["content"]

    assert [m["role"] for m in dressed] == ["system", "user"], \
        f"流式这条插出了第二条 system：{dressed}"
    assert dressed[0]["content"].startswith(PERSONA), \
        f"流式这条把 persona 覆盖/挪走了：{dressed[0]['content']!r}"
    assert "今天是 2026-09-22 周二。" in dressed[0]["content"][len(PERSONA):], \
        dressed[0]["content"]
