"""流式输出模块 - 基于 Server-Sent Events (SSE)。

模型配置统一来自 app.core.providers，不再自带一份模型清单——此前
streaming 与 llm_client 各有一份 MODEL_CONFIG，改一处不生效；且未知模型会
静默回落到 deepseek-chat，造成"界面显示 GPT-4o、实际是 DeepSeek 在答"。
"""
from typing import Generator, List, Dict, Any

from app.core.providers import store, build_client


def stream_chat(
    model: str,
    messages: List[Dict[str, Any]],
    provider_id: str = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
) -> Generator[str, None, None]:
    """流式生成 AI 回复，逐块产出文本。

    异常一律向上抛出：若在此转成文本 yield，调用方无法区分正常回答与故障，
    错误文本还会被写进会话历史。

    这里必须是同步生成器而不是 async：`for chunk in stream` 走的是阻塞 socket。
    async 生成器会被事件循环直接驱动，一个卡住的上游就冻住整个进程（连 /health
    都不响应，Cloudflare 报 524）；同步生成器才会被
    StreamingResponse 的 iterate_in_threadpool 放进线程池。
    """
    provider = store.resolve(provider_id, legacy_model=model)
    client = build_client(provider)

    stream = client.chat.completions.create(
        model=provider["model"],
        messages=messages,
        stream=True,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
