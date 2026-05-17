import os
from dotenv import load_dotenv
from openai import OpenAI

# 加载 .env 文件里的密钥
load_dotenv()

# 初始化 DeepSeek 客户端（用 OpenAI 库兼容模式）
client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com/v1"
)

# 定义工具列表，这里只给一个“获取天气”的虚拟工具
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "获取指定城市的天气",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "城市名，如 Beijing"
                    }
                },
                "required": ["city"]
            }
        }
    }
]

# 给模型的提示：要求它查天气
messages = [{"role": "user", "content": "请问北京今天天气怎么样？"}]

# 调用 API，并告诉模型它可以使用的工具
response = client.chat.completions.create(
    model="deepseek-chat",
    messages=messages,
    tools=tools,
    tool_choice="auto"  # 让模型自己决定用不用工具
)

# 打印模型返回的结果
msg = response.choices[0].message
print("模型回复：", msg)
print("是否有工具调用？", msg.tool_calls)
if msg.tool_calls:
    for tool_call in msg.tool_calls:
        print("要调用的工具名：", tool_call.function.name)
        print("参数：", tool_call.function.arguments)
        import json

# 假设我们从模型拿到了 tool_calls
tool_call = msg.tool_calls[0]
function_name = tool_call.function.name
arguments = json.loads(tool_call.function.arguments)  # 把字串变字典

# 模拟执行工具
if function_name == "get_weather":
    city = arguments["city"]
    # 实际这里该去调天气API，我们造假数据
    weather_result = f"{city}今天晴朗，25°C"
    print("工具执行结果：", weather_result)

# 把工具调用结果回填消息列表
messages.append(msg.model_dump())  # 把模型的函数调用响应加入历史
messages.append({
    "role": "tool",
    "tool_call_id": tool_call.id,
    "content": weather_result
})

# 再次调用模型，让它基于工具结果生成最终回答
final_response = client.chat.completions.create(
    model="deepseek-chat",
    messages=messages
)
print("最终回答：", final_response.choices[0].message.content)