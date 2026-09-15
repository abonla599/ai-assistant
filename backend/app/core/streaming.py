"""
流式输出模块 - 基于 Server-Sent Events (SSE)
"""
import os
import asyncio
from typing import AsyncGenerator
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()


# 模型配置映射
MODEL_CONFIG = {
    "deepseek-chat": {
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com/v1",
        "model_name": "deepseek-chat"
    },
    "gpt-4o": {
        "api_key_env": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
        "model_name": "gpt-4o"
    },
    "gpt-3.5-turbo": {
        "api_key_env": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
        "model_name": "gpt-3.5-turbo"
    }
}


async def stream_chat(model: str, messages: list) -> AsyncGenerator[str, None]:
    """
    流式生成 AI 回复
    每次产出文本片段
    """
    # 获取模型配置
    config = MODEL_CONFIG.get(model, MODEL_CONFIG["deepseek-chat"])

    api_key = os.getenv(config["api_key_env"])
    if not api_key:
        raise RuntimeError(f"缺少 API Key: {config['api_key_env']}")

    client = OpenAI(
        api_key=api_key,
        base_url=config["base_url"]
    )

    # 发起流式请求
    stream = client.chat.completions.create(
        model=config["model_name"],
        messages=messages,
        stream=True,
        temperature=0.7,
        max_tokens=4096
    )

    # 逐块产出内容。异常一律向上抛出：若在这里被转成文本 yield，
    # 调用方无法区分正常回答与故障，错误文本还会被写进会话历史。
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


async def stream_chat_with_tools(
    model: str,
    messages: list,
    tool_calls_enabled: bool = True
) -> AsyncGenerator[str, None]:
    """
    支持工具调用的流式输出（扩展版本）
    当检测到工具调用时，会暂停流式输出，执行工具后继续
    """
    # 这里可以集成你的工具调用逻辑
    # 简单实现：直接使用普通的流式输出
    async for chunk in stream_chat(model, messages):
        yield chunk