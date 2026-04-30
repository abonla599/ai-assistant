import math
import re
from app.tools.registry import register_tool
from duckduckgo_search import DDGS

# ---------- 计算器工具 ----------
@register_tool(
    name="calculator",
    description="执行数学计算，支持加减乘除、乘方、开方等。输入表达式字符串。",
    parameters={
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "数学表达式，如 '2+3*(4-1)' 或 'sqrt(16)'"
            }
        },
        "required": ["expression"]
    }
)
def calculator(expression: str) -> str:
    # 安全的白名单：只允许数字、运算符、括号、math模块中的函数
    allowed = set("0123456789+-*/(). ")
    safe = True
    for ch in expression:
        if ch not in allowed and not ch.isalpha():
            safe = False
            break
    if not safe:
        return "表达式包含不允许的字符，仅支持基本数学运算和 math 函数。"
    try:
        # 将可能的 math 函数前缀补全
        eval_names = {name: getattr(math, name) for name in dir(math) if callable(getattr(math, name))}
        eval_names["__builtins__"] = None
        result = eval(expression, {"__builtins__": None}, eval_names)
        return str(result)
    except Exception as e:
        return f"计算错误: {e}"

# ---------- 搜索引擎工具 ----------
@register_tool(
    name="web_search",
    description="搜索互联网获取实时信息。输入搜索关键词。",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词"
            }
        },
        "required": ["query"]
    }
)
def web_search(query: str) -> str:
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3))
        if not results:
            return "未找到任何结果。"
        summaries = [f"- {r['title']}: {r['body']}" for r in results]
        return "\n".join(summaries)
    except Exception as e:
        return f"搜索失败: {e}"