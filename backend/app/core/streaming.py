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
        yield f"[错误] 缺少 API Key: {config['api_key_env']}"
        return

    client = OpenAI(
        api_key=api_key,
        base_url=config["base_url"]
    )

    try:
        # 发起流式请求
        stream = client.chat.completions.create(
            model=config["model_name"],
            messages=messages,
            stream=True,
            temperature=0.7,
            max_tokens=4096
        )

        # 逐块产出内容
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    except Exception as e:
        yield f"\n[流式错误] {str(e)}"


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