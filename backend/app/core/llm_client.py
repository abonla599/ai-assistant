"""LLM 客户端 - 统一的非流式模型调用接口。

模型配置一律取自 app.core.providers。此前本模块自带一份 MODEL_CONFIGS，与
streaming 里的那份重复且互不同步，是"界面模型与实际调用不一致"的根源。
"""
from typing import List, Dict, Optional, Any

from app.core.providers import store, build_client


def get_llm_response(
    model: str,
    messages: List[Dict[str, Any]],
    temperature: float = 0.7,
    tools: Optional[List[Dict]] = None,
    provider_id: str = None,
) -> str:
    """统一 LLM 调用。

    失败时抛出异常而不是返回错误文本：把故障当正常回复返回，会让错误被写进
    会话历史、并被上层当作模型输出继续加工。
    """
    provider = store.resolve(provider_id, legacy_model=model)
    client = build_client(provider)

    params: Dict[str, Any] = {
        "model": provider["model"],
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        params["tools"] = tools

    response = client.chat.completions.create(**params)
    return response.choices[0].message.content


def get_llm_response_with_tools(
    model: str,
    messages: List[Dict[str, Any]],
    tools: List[Dict],
    temperature: float = 0.7,
    provider_id: str = None,
) -> dict:
    """带工具调用的 LLM 接口，返回 {content, tool_calls}。"""
    provider = store.resolve(provider_id, legacy_model=model)
    client = build_client(provider)

    response = client.chat.completions.create(
        model=provider["model"],
        messages=messages,
        tools=tools,
        temperature=temperature,
    )

    choice = response.choices[0].message
    return {
        "content": choice.content,
        "tool_calls": None if not choice.tool_calls else [
            {
                "id": tc.id,
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in choice.tool_calls
        ],
    }
