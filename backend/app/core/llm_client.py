import os
from dotenv import load_dotenv
from openai import OpenAI

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

def get_llm_response(model: str, messages: list, temperature: float = 0.7) -> str:
    """
    根据模型名调用对应的 LLM 并返回回复文本。
    model: 模型标识，如 "deepseek-chat"
    messages: 标准对话列表 [{"role": "user", "content": "..."}, ...]
    """
    if model not in MODEL_CONFIGS:
        raise ValueError(f"不支持的模型: {model}。可选: {list(MODEL_CONFIGS.keys())}")

    config = MODEL_CONFIGS[model]
    api_key = os.getenv(config["api_key_env"])
    if not api_key:
        raise ValueError(f"缺少 API Key：请在 .env 中设置 {config['api_key_env']}")

    # 初始化客户端（所有模型都兼容 OpenAI SDK）
    client = OpenAI(
        api_key=api_key,
        base_url=config["base_url"]
    )

    response = client.chat.completions.create(
        model=config["model_name"],
        messages=messages,
        temperature=temperature
    )

    return response.choices[0].message.content