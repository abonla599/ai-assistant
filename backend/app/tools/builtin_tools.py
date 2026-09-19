import sys
import os
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir)) # 指向 backend 目录

# 如果 project_root 不在 sys.path 中，则添加
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from app.sandbox.sandbox_manager import SandboxManager
import ast
import math
import operator
import re
import time
from app.tools import availability
from app.tools.registry import register_tool, tools_registry, is_available
from ddgs import DDGS
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
    # 连续运算符：`2++3` 在 Python 里其实合法（等于 5），模型写成这样几乎总是它自己
    # 也没想清楚，与其替它猜不如退回重写。`**` 不在这条规则里——那是乘方。
    if re.search(r"[+\-/%]\s*[+\-/%]", expression):
        return ToolResponse(False, error="语法错误", hint="请提供合法的数学表达式，如 2+3*4")

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return ToolResponse(False, error="语法错误", hint="请提供合法的数学表达式，如 2+3*4")

    try:
        return ToolResponse(True, data=_value_of(tree.body))
    except _Refusal as e:
        return ToolResponse(False, error=str(e),
                            hint="仅支持四则运算、乘方，以及白名单内的 math 函数")


class _Refusal(ValueError):
    """表达式里有白名单之外的东西。拒绝是默认分支，不是例外分支。"""


_MATH_FUNCTIONS = {"sin", "cos", "tan", "asin", "acos", "atan", "atan2", "sqrt",
                   "log", "log2", "log10", "exp", "pow", "factorial", "ceil", "floor"}
_MATH_CONSTANTS = {"pi", "e", "tau", "inf"}
_BIN_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
            ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
            ast.Mod: operator.mod, ast.Pow: operator.pow}

# 上限落在求值之前。算完再嫌大，CPU 已经付过了；而一个百万位的整数即使能算出来，
# 也会整块进模型上下文。
_MAX_ARG = 1000
_MAX_EXPONENT = 64


def _value_of(node):
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _value_of(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value

    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left, right = _value_of(node.left), _value_of(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_EXPONENT:
            raise _Refusal(f"指数过大：上限是 {_MAX_EXPONENT}")
        try:
            return _BIN_OPS[type(node.op)](left, right)
        except ZeroDivisionError:
            raise _Refusal("除数为零")
        except OverflowError:
            raise _Refusal("结果溢出")

    if isinstance(node, ast.Attribute):
        if not (isinstance(node.value, ast.Name) and node.value.id == "math"):
            raise _Refusal("只能读 math 模块下的常量")
        if node.attr not in _MATH_CONSTANTS:
            raise _Refusal(f"math.{node.attr} 不在白名单里")
        return getattr(math, node.attr)

    if isinstance(node, ast.Call):
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
                and fn.value.id == "math"):
            raise _Refusal("只能调用 math 模块下的函数")
        if fn.attr not in _MATH_FUNCTIONS:
            raise _Refusal(f"math.{fn.attr} 不在白名单里")
        if node.keywords:
            raise _Refusal("不支持关键字参数")
        args = [_value_of(a) for a in node.args]
        for arg in args:
            if abs(arg) > _MAX_ARG:
                raise _Refusal(f"参数过大：math.{fn.attr} 的数值上限是 {_MAX_ARG}")
        try:
            return getattr(math, fn.attr)(*args)
        except (TypeError, ValueError, OverflowError) as e:
            raise _Refusal(f"math.{fn.attr} 调用失败：{e}")

    raise _Refusal("表达式包含不支持的写法")

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
    },
    # 这里必须是 availability 模块属性调用（而不是 from ... import search_reachable），
    # 也不能在导入期求值：搜索源通不通是后台每 5 分钟重测一次的，导入期定死就退化成名单。
    available=lambda: availability.search_reachable()
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
        # 模型问"你有什么工具"时推荐一个必失败的，等于我们亲自给它挖坑：
        # 它会照着调，拿回一句错误，再凭那句错误硬编答案。
        if not is_available(t):
            continue
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
    },
    # 读 sandbox 这一个实例的 client，不再 new 一个 SandboxManager：后者每次都会
    # 重连 docker 守护进程，并把「沙箱停用」那行警告重复打印一遍。
    available=lambda: sandbox.client is not None
)
def execute_code(code: str, language: str = "python", max_retries=2) -> str:
    result = {}
    for attempt in range(max_retries + 1):
        result = sandbox.run_code(code, language)
        if not result.get("error"):
            break
        if attempt < max_retries:
            time.sleep(0.5)

    # 沙箱返回的是字典，里面有 stdout, stderr, error
    if result.get("error"):
        # 如果沙箱本身报错（比如超时、容器启动失败）
        return f"执行错误: {result['error']}"

    # 正常情况拼接标准输出和标准错误输出
    out = result.get("stdout", "")
    err = result.get("stderr", "")
    # 非零退出码必须让模型看见：否则"python 连文件都没打开"也会被包成 ✓ 成功。
    # 不写进 error 字段——那是沙箱基础设施的重试判据，用户代码自己的报错重试三次
    # 既不会变对，又白烧三个容器。
    exit_code = result.get("exit_code")
    tail = f"\n退出码: {exit_code}" if exit_code else ""
    # 返回给模型的文本（模型会看到这个字符串）
    return f"输出:\n{out}\n错误:\n{err}{tail}"