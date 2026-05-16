"""
LLM 客户端 - 统一的模型调用接口
"""
import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

MODEL_CONFIGS = {
    "deepseek-chat": {
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com",
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

def get_llm_response(model: str, messages: list, temperature: float = 0.7) -> str:
    """
    统一的 LLM 调用接口
    
    参数:
        model: 模型标识符，如 "deepseek-chat", "gpt-4o"
        messages: 消息列表 [{"role": "user", "content": "..."}]
        temperature: 温度参数 0-1
    
    返回:
        str: 模型回复的内容
    """
    config = MODEL_CONFIGS.get(model)
    if not config:
        return f"不支持的模型: {model}。可用模型: {list(MODEL_CONFIGS.keys())}"
    
    api_key = os.getenv(config["api_key_env"])
    if not api_key:
        return f"缺少API密钥: {config['api_key_env']}，请在 .env 文件中设置"
    
    try:
        client = OpenAI(api_key=api_key, base_url=config["base_url"])
        response = client.chat.completions.create(
            model=config["model_name"],
            messages=messages,
            temperature=temperature
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"LLM调用失败: {str(e)}"