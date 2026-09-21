"""流式输出模块 - 基于 Server-Sent Events (SSE)。

模型配置统一来自 app.core.providers，不再自带一份模型清单——此前
streaming 与 llm_client 各有一份 MODEL_CONFIG，改一处不生效；且未知模型会
静默回落到 deepseek-chat，造成"界面显示 GPT-4o、实际是 DeepSeek 在答"。
"""
import json
from typing import Generator, List, Dict, Any, Optional

from app.core.providers import store, build_client
from app.core import usage   # 账本：两条聊天路径共用一个口径，别各记一套
from app.tools.executor import execute_tool


def stream_chat(
    model: str,
    messages: List[Dict[str, Any]],
    provider_id: str = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    tools: Optional[List[Dict]] = None,
    max_tool_turns: int = 5,
    user_id: str = None,
) -> Generator[str, None, None]:
    """流式生成 AI 回复，逐块产出文本；给了 tools 就带上工具循环。

    异常一律向上抛出：若在此转成文本 yield，调用方无法区分正常回答与故障，
    错误文本还会被写进会话历史。

    这里必须是同步生成器而不是 async：`for chunk in stream` 走的是阻塞 socket。
    async 生成器会被事件循环直接驱动，一个卡住的上游就冻住整个进程（连 /health
    都不响应，Cloudflare 报 524）；同步生成器才会被
    StreamingResponse 的 iterate_in_threadpool 放进线程池。

    tools 默认 None 而不是自动装配：非流式那条路（ChatPipeline）一直是把工具传给
    模型的，这里曾经一个 tools 形参都没有，于是只走流式的界面上，工具等于不存在——
    模型如实回答"我无法调用外部计算器工具"。要不要给工具由调用方决定。
    """
    provider = store.resolve(provider_id, legacy_model=model)
    client = build_client(provider)

    # 复制一份：工具轮次要往里追加 assistant/tool 消息，不能改调用方的列表
    msgs = list(messages)
    turns = max_tool_turns if tools else 1

    billed: Dict[str, int] = {k: 0 for k in ("prompt_tokens", "completion_tokens",
                                             "total_tokens", "reasoning_tokens", "cached_tokens")}
    rounds = 0
    ok = True
    try:
        for _ in range(turns):
            kwargs: Dict[str, Any] = {
                "model": provider["model"],
                "messages": msgs,
                "stream": True,
                "temperature": temperature,
                "max_tokens": max_tokens,
                # 没有这一行，流式响应里永远不会有 usage，账本只能记"不知道"。
                # 网关若因此回 4xx，它会以 failed 出现在账上——那正是这本账要看得见的事。
                "stream_options": {"include_usage": True},
            }
            if tools:
                # 空数组不传：有些兼容网关对 tools=[] 直接 400
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"

            stream = client.chat.completions.create(**kwargs)

            text = ""
            # 工具调用在流式里是按 index 分片回来的：id/name 通常只在第一片，
            # arguments 一串 JSON 被切成任意多片，所以必须按 index 累积再解析。
            calls: Dict[int, Dict[str, str]] = {}

            rounds += 1
            for chunk in stream:
                # usage 通常在最后一个块里，而那个块的 choices 是空数组——
                # 先读账再 continue，否则永远读不到。
                u = getattr(chunk, "usage", None)
                if u is not None:
                    billed["prompt_tokens"] += getattr(u, "prompt_tokens", 0) or 0
                    billed["completion_tokens"] += getattr(u, "completion_tokens", 0) or 0
                    billed["total_tokens"] += getattr(u, "total_tokens", 0) or 0
                    details = getattr(u, "completion_tokens_details", None)
                    billed["reasoning_tokens"] += (getattr(details, "reasoning_tokens", 0) or 0) if details else 0
                    pdetails = getattr(u, "prompt_tokens_details", None)
                    billed["cached_tokens"] += (getattr(pdetails, "cached_tokens", 0) or 0) if pdetails else 0
                if not getattr(chunk, "choices", None):
                    continue
                delta = getattr(chunk.choices[0], "delta", None)
                if delta is None:
                    continue
                piece = getattr(delta, "content", None)
                if piece:
                    text += piece
                    yield piece
                for tc in (getattr(delta, "tool_calls", None) or []):
                    slot = calls.setdefault(getattr(tc, "index", 0) or 0,
                                            {"id": "", "name": "", "arguments": ""})
                    if getattr(tc, "id", None):
                        slot["id"] = tc.id
                    fn = getattr(tc, "function", None)
                    if fn is not None:
                        if getattr(fn, "name", None):
                            slot["name"] += fn.name
                        if getattr(fn, "arguments", None):
                            slot["arguments"] += fn.arguments

            if not calls:
                return

            msgs.append({
                "role": "assistant",
                "content": text or None,
                "tool_calls": [
                    {"id": slot["id"], "type": "function",
                     "function": {"name": slot["name"], "arguments": slot["arguments"]}}
                    for _, slot in sorted(calls.items())
                ],
            })

            for _, slot in sorted(calls.items()):
                # 与 pipeline.py 那行 [Pipeline] 调用工具 同一用途：这条路上曾经静默了
                # 几个月，出事只能靠猜"到底有没有执行"。
                print(f"[Stream] 调用工具: {slot['name']}({slot['arguments']})", flush=True)
                result = _run_tool(slot["name"], slot["arguments"], user_id=user_id)
                msgs.append({"role": "tool", "tool_call_id": slot["id"],
                             "content": result})


    except BaseException:
        ok = False
        raise
    finally:
        # 客户端中途断开走的是 GeneratorExit，也会落到这里。那一半的消耗同样要记账，
        # 而且要记成 failed——只记跑完的那一次会系统性低估，也永远看不见故障率。
        usage.record_call(user_id=user_id or "unattributed",
                          provider_id=provider["id"],
                          paid_by=provider.get("paid_by") or "operator",
                          tool_rounds=max(0, rounds - 1), ok=ok, **billed)

def _run_tool(name: str, raw_arguments: str, user_id: str = None) -> str:
    """执行一个工具调用，永远回一个字符串。

    工具炸了不能把整条 SSE 打断：那时用户看到的是半句话加一个断流，而模型永远
    不知道自己哪里没算成。把错误当作工具结果回灌，模型才能解释它。
    """
    try:
        args = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError as e:
        return f"工具参数不是合法 JSON，没能执行：{e}"
    try:
        return str(execute_tool(name, args, user_id=user_id))
    except Exception as e:  # 工具内部任何异常都只影响这一次调用
        return f"工具执行错误: {e}"
