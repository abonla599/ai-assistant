import math
import re
from app.tools.registry import register_tool
from ddgs import DDGS
from app.tools.registry import register_tool, tools_registry
from app.tools.response import ToolResponse 
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
def calculator(expression: str) -> ToolResponse:
    # 允许的字符和模式检查
    allowed_chars = set("0123456789+-*/().% ^<>=!|&")
    # 更安全的 eval 使用 ast.literal_eval 仅支持字面量，但需要数学运算，这里仍用 eval 但加强限制
    # 简单过滤危险内置函数
    if any(forbidden in expression for forbidden in ['__', 'import', 'os', 'sys', 'subprocess']):
        return ToolResponse(False, error="表达式包含禁止的操作", hint="仅支持基本数学运算和 math 函数")
    try:
        # 使用安全的数学执行环境
        import math
        safe_dict = {"__builtins__": None, "math": math}
        result = eval(expression, safe_dict, {})
        return ToolResponse(True, data=result)
    except SyntaxError:
        return ToolResponse(False, error="语法错误", hint="请提供合法的数学表达式，如 2+3*4")
    except Exception as e:
        return ToolResponse(False, error=str(e), hint="检查表达式或尝试简化")

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
@register_tool(
    name="help",
    description="查看当前可用的工具列表及其用途",
    parameters={
        "type": "object",
        "properties": {},
        "required": []
    }
)
def help_tool() -> str:
    info = []
    for name, t in tools_registry.items():
        info.append(f"{name}: {t['description']}")
    return "\n".join(info)
