import sys
import os

# 获取当前文件所在目录 (backend)
current_dir = os.path.dirname(os.path.abspath(__file__))
# 如果 app 目录在当前目录下，则当前目录即为根路径
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)
# 导入 pytest（虽然不强制，但明确使用）
import pytest

# 导入我们要测试的函数 execute_tool
from app.tools.executor import execute_tool

# 导入 builtin_tools，这会触发所有工具的注册（非常重要）
import app.tools.builtin_tools
def test_calculator_valid():
    """测试计算器正常计算 2+3，应该返回结果包含 5"""
    result = execute_tool("calculator", {"expression": "2+3"})
    # 根据你的统一返回格式，成功时会以 ✓ 开头
    assert "✓" in result
    assert "5" in result
def test_calculator_invalid_syntax():
    """测试计算器面对非法表达式 2++3 应返回错误提示"""
    result = execute_tool("calculator", {"expression": "2++3"})
    assert "✗" in result
    # 不要求具体错误信息，只要有错误标记就行
def test_calculator_forbidden():
    """测试尝试执行危险代码，应被拦截"""
    result = execute_tool("calculator", {"expression": "__import__('os')"})
    assert "禁止" in result or "✗" in result

def test_web_search():
    """测试搜索 'Python'，结果应包含 Python 字样"""
    result = execute_tool("web_search", {"query": "Python"})
    # 成功应有 ✓ 并且包含搜索词
    assert "✓" in result
    assert "Python" in result

def test_help():
    """测试 help 工具是否列出已知工具"""
    result = execute_tool("help", {})
    assert "calculator" in result
    assert "web_search" in result
    assert "execute_code" in result
def test_unknown_tool():
    """测试调用一个不存在的工具，应返回错误"""
    result = execute_tool("nonexistent", {})
    assert "未知" in result or "✗" in result
def test_code_tool_python():
    """测试用 Python 执行 print('hello')，应输出 hello"""
    result = execute_tool("execute_code", {
        "code": "print('hello')",
        "language": "python"
    })
    assert "hello" in result
    # 成功不应包含错误标记（如果沙箱正常）
    assert "✗" not in result
def test_code_tool_javascript():
    """测试用 JavaScript 执行 console.log('hi')"""
    result = execute_tool("execute_code", {
        "code": "console.log('hi from js');",
        "language": "javascript"
    })
    assert "hi from js" in result
def test_code_tool_syntax_error():
    """测试写错语法的 Python 代码，应返回错误信息（包含 stderr 或 error）"""
    result = execute_tool("execute_code", {
        "code": "prin('typo')",
        "language": "python"
    })
    # 应该有错误输出，不要求精确，但结果里应该有 NameError 或 错误 字样
    assert "NameError" in result or "错误" in result or "error" in result



def test_code_tool_timeout():
    """测试死循环代码应触发超时并返回错误"""
    result = execute_tool("execute_code", {
        "code": "while True: pass",
        "language": "python"
    })
    assert "超时" in result or "timeout" in result or "错误" in result