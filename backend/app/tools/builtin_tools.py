import sys
import os
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir)) # 指向 backend 目录

# 如果 project_root 不在 sys.path 中，则添加
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from app.sandbox.sandbox_manager import SandboxManager
import math
import re
from app.tools.registry import register_tool
from ddgs import DDGS
from app.tools.registry import register_tool, tools_registry
from app.tools.response import ToolResponse 
# ---------- 计算器工具 ----------
sandbox = SandboxManager()
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
    
    # 简单过滤危险内置函数
    if any(forbidden in expression for forbidden in ['__', 'import', 'os', 'sys', 'subprocess']):
        return ToolResponse(False, error="表达式包含禁止的操作", hint="仅支持基本数学运算和 math 函数")

    # 预检查：不允许连续的运算符（如 ++, --, +-, etc.）
    if re.search(r'[+\-*/%]\s*[+\-*/%]', expression):
        return ToolResponse(False, error="语法错误", hint="请提供合法的数学表达式，如 2+3*4")

    try:
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


@register_tool(
    name="execute_code",   # 工具名，模型会叫这个名字
    description="执行一段代码并返回输出。支持 python 和 javascript。",
    parameters={
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "要执行的代码，注意必须是完整可运行的"
            },
            "language": {
                "type": "string",
                "enum": ["python", "javascript"],   # 只能选这两个
                "default": "python",
                "description": "编程语言，默认 python"
            }
        },
        "required": ["code"]   # 必须提供代码，语言不提供则默认为 python
    }
)
def execute_code(code: str, language: str = "python", max_retries=2) -> str:
    for attempt in range(max_retries + 1):
        result = sandbox.run_code(code, language)
        if not result.get("error"):
            break
        if attempt < max_retries:
            import time
            time.sleep(0.5)
  # 调用沙箱管理器的 run_code 方法
    result = sandbox.run_code(code, language)

    # 沙箱返回的是字典，里面有 stdout, stderr, error
    if result.get("error"):
        # 如果沙箱本身报错（比如超时、容器启动失败）
        return f"执行错误: {result['error']}"

    # 正常情况拼接标准输出和标准错误输出
    out = result.get("stdout", "")
    err = result.get("stderr", "")
    # 返回给模型的文本（模型会看到这个字符串）
    return f"输出:\n{out}\n错误:\n{err}"