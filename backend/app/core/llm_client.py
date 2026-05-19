"""
LLM 客户端 - 统一的模型调用接口
支持 DeepSeek、OpenAI 等多种模型
"""
import os
from dotenv import load_dotenv
from openai import OpenAI
from typing import List, Dict, Optional

load_dotenv()

# 所有支持的模型及其配置
MODEL_CONFIGS = {
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


def get_llm_response(
    model: str,
    messages: List[Dict[str, str]],
    temperature: float = 0.7,
    tools: Optional[List[Dict]] = None
) -> str:
    """
    统一的 LLM 调用接口
    
    参数:
        model: 模型标识符，如 "deepseek-chat", "gpt-4o"
        messages: 消息列表 [{"role": "user", "content": "..."}]
        temperature: 温度参数 0-1
        tools: 可选，工具定义列表（用于 Function Calling）
    
    返回:
        str: 模型回复的文本内容
    """
    # 检查模型是否支持
    config = MODEL_CONFIGS.get(model)
    if not config:
        supported = list(MODEL_CONFIGS.keys())
        return f"不支持的模型: {model}。可用模型: {supported}"
    
    # 获取 API Key
    api_key = os.getenv(config["api_key_env"])
    if not api_key:
        return f"缺少 API 密钥: {config['api_key_env']}，请在 .env 文件中设置"
    
    try:
        # 初始化客户端
        client = OpenAI(
            api_key=api_key,
            base_url=config["base_url"]
        )
        
        # 构建请求参数
        request_params = {
            "model": config["model_name"],
            "messages": messages,
            "temperature": temperature
        }
        
        # 如果提供了工具定义，添加到请求中
        if tools:
            request_params["tools"] = tools
        
        # 调用 API
        response = client.chat.completions.create(**request_params)
        
        # 返回回复内容
        return response.choices[0].message.content
        
    except Exception as e:
        return f"LLM 调用失败: {str(e)}"


def get_llm_response_with_tools(
    model: str,
    messages: List[Dict[str, str]],
    tools: List[Dict],
    temperature: float = 0.7
) -> dict:
    """
    带工具调用的 LLM 接口
    返回完整的响应对象，包含可能的 tool_calls
    
    参数:
        model: 模型标识符
        messages: 消息列表
        tools: 工具定义列表
        temperature: 温度参数
    
    返回:
        dict: {
            "content": str or None,  # 文本回复
            "tool_calls": list or None  # 工具调用列表
        }
    """
    config = MODEL_CONFIGS.get(model)
    if not config:
        return {"content": f"不支持的模型: {model}", "tool_calls": None}
    
    api_key = os.getenv(config["api_key_env"])
    if not api_key:
        return {"content": f"缺少 API 密钥: {config['api_key_env']}", "tool_calls": None}
    
    try:
        client = OpenAI(
            api_key=api_key,
            base_url=config["base_url"]
        )
        
        response = client.chat.completions.create(
            model=config["model_name"],
            messages=messages,
            tools=tools,
            temperature=temperature
        )
        
        choice = response.choices[0]
        result = {
            "content": choice.message.content,
            "tool_calls": None
        }
        
        # 检查是否有工具调用
        if choice.message.tool_calls:
            result["tool_calls"] = [
                {
                    "id": tc.id,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments
                    }
                }
                for tc in choice.message.tool_calls
            ]
        
        return result
        
    except Exception as e:
        return {"content": f"LLM 调用失败: {str(e)}", "tool_calls": None}