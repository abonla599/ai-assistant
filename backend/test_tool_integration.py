import json
from dotenv import load_dotenv
from openai import OpenAI
import os
from app.tools.registry import get_all_tools_schema
from app.tools.executor import execute_tool
import app.tools.builtin_tools  # 导入即注册

load_dotenv()
client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com/v1"
)

tools_schema = get_all_tools_schema()
messages = [{"role": "user", "content": "帮我算一下 335*567，然后搜索一下这个数字的含义"}]

# 第一次调用：模型可能返回函数调用
response = client.chat.completions.create(
    model="deepseek-chat",
    messages=messages,
    tools=tools_schema,
    tool_choice="auto"
)

msg = response.choices[0].message
print("第一次模型回复：", msg)

# 如果模型要求调用工具，则执行并回传结果
while msg.tool_calls:
    messages.append(msg.model_dump())  # 添加助手消息（含 tool_calls）
    for tool_call in msg.tool_calls:
        name = tool_call.function.name
        args = json.loads(tool_call.function.arguments)
        print(f"调用工具: {name}, 参数: {args}")
        result = execute_tool(name, args)
        print(f"工具返回: {result}")
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": result
        })
    # 再次请求模型
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=messages,
        tools=tools_schema,
        tool_choice="auto"
    )
    msg = response.choices[0].message

print("最终回答：", msg.content)