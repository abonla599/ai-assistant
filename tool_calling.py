import json
from datetime import datetime
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
import os

load_dotenv()

def get_current_time() -> str:
    """获取当前日期和时间"""
    now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S")

tools = [get_current_time]

llm = ChatOpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com/v1",
    model="deepseek-chat"
)

# 将工具绑定到模型上
llm_with_tools = llm.bind_tools(tools)

if __name__ == "__main__":
    messages = [{"role": "user", "content": "现在几点了？"}]
    response = llm_with_tools.invoke(messages)
    
    # 检查模型是否发起了工具调用
    if response.tool_calls:
        for tool_call in response.tool_calls:
            if tool_call["name"] == "get_current_time":
                print(f"🧠 模型指令: 调用 'get_current_time' 工具，参数: {tool_call['args']}")
                
                # 执行工具
                result = get_current_time()
                print(f"✅ 工具执行结果: {result}")
                
                # 把执行结果返回给AI模型
                messages.append(response) # 添加模型的工具调用消息
                messages.append({
                    "role": "tool",
                    "content": json.dumps({"result": result}),
                    "tool_call_id": tool_call["id"]
                })
                
                # 模型基于工具结果生成最终答案
                final_response = llm_with_tools.invoke(messages)
                print(f"🤖 AI最终回答: {final_response.content}")
    else:
        # 如果模型直接回答，就打印出来
        print(f"🤖 AI直接回答: {response.content}")