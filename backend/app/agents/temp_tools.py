"""
刘吉洋多智能体测试用临时工具
完全不依赖费丙乾的工具模块
"""

def calculator(expression: str) -> str:
    """安全计算数学表达式"""
    try:
        import math
        allowed = {"__builtins__": {}, "math": math}
        result = eval(expression, allowed, {})
        return f"计算结果: {result}"
    except Exception as e:
        return f"计算错误: {e}"

def web_search(query: str) -> str:
    """模拟搜索（返回预设结果）"""
    fake_db = {
        "python": "Python 是一种广泛使用的解释型、高级编程语言，以简洁易读著称。",
        "火箭回收": "火箭回收技术主要由 SpaceX 主导，已实现猎鹰9号一级助推器多次成功回收。",
        "马斯克": "埃隆·马斯克是 SpaceX、特斯拉、Neuralink、xAI 等多家公司的 CEO 或创始人。",
        "人工智能": "AI 是计算机科学分支，致力于创建能执行需要人类智能的任务的系统。",
        "特斯拉": "特斯拉是全球领先的电动汽车和清洁能源公司，总部在美国德州。",
        "spacex": "SpaceX 是一家美国航天制造商和太空运输公司，创始人埃隆·马斯克。"
    }
    for key, value in fake_db.items():
        if key in query.lower():
            return value
    return f"未找到与 '{query}' 相关的信息。（模拟搜索）"

def note_taking(content: str) -> str:
    """模拟记录笔记"""
    with open("/tmp/agent_notes.txt", "a", encoding="utf-8") as f:
        f.write(content + "\n")
    return f"已记录: {content[:60]}..."

def time_query(_: str = "") -> str:
    """返回当前日期时间"""
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ============ 工具注册表 ============
TEMP_TOOLS = {
    "calculator": {
        "function": calculator,
        "description": "执行数学计算，输入数学表达式字符串",
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "数学表达式，如 2+3*4"}
            },
            "required": ["expression"]
        }
    },
    "web_search": {
        "function": web_search,
        "description": "搜索互联网获取信息",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"}
            },
            "required": ["query"]
        }
    },
    "note_taking": {
        "function": note_taking,
        "description": "记录笔记，保存重要信息",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "要记录的内容"}
            },
            "required": ["content"]
        }
    },
    "time_query": {
        "function": time_query,
        "description": "查询当前日期和时间",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
}

def get_temp_tools_schema():
    """返回 OpenAI 格式的工具 schema 列表"""
    schema = []
    for name, tool in TEMP_TOOLS.items():
        schema.append({
            "type": "function",
            "function": {
                "name": name,
                "description": tool["description"],
                "parameters": tool["parameters"]
            }
        })
    return schema

def execute_temp_tool(name: str, arguments: dict) -> str:
    """执行临时工具"""
    if name not in TEMP_TOOLS:
        return f"未知工具: {name}"
    func = TEMP_TOOLS[name]["function"]
    try:
        return func(**arguments)
    except Exception as e:
        return f"工具执行错误: {e}"