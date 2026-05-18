import sys
import os
import json

sys.path.insert(0, os.path.dirname(__file__))

from app.agents.react_agent import ReActAgent

# ========== 临时假工具 ==========
def calculator(expression: str) -> str:
    try:
        result = eval(expression, {"__builtins__": {}}, {})
        return str(result)
    except Exception as e:
        return f"计算错误: {e}"

def web_search(query: str) -> str:
    return f"关于「{query}」的搜索结果：这是一个模拟的搜索结果，256 的平方根是 16，16 在数学中是一个完全平方数。"

tools_dict = {
    "calculator": {
        "function": calculator,
        "description": "执行数学计算",
        "parameters": {}
    },
    "web_search": {
        "function": web_search,
        "description": "搜索互联网",
        "parameters": {}
    }
}

tools_schema = [
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "执行数学计算，输入表达式字符串",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "数学表达式，如 '2+3'"}
                },
                "required": ["expression"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "搜索互联网获取信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"}
                },
                "required": ["query"]
            }
        }
    }
]

# ========== 创建 Agent ==========
agent = ReActAgent(
    name="小助手",
    model="deepseek-chat",
    tools_schema=tools_schema,
    system_prompt="你是一个能使用工具解决问题的助手。需要计算或搜索时请调用对应工具。一步步思考，先规划再行动。"
)
agent.tools = tools_dict

# ========== 运行测试 ==========
result = agent.run("计算 256 的平方根，然后搜索一下这个数字有什么特别的含义")
print("\n" + "=" * 50)
print("最终结果：")
print(result)