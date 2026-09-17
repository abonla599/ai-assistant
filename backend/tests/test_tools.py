"""工具分发与表达式净化的回归锁。

本文件原先躺在 backend/ 根目录（CI 只跑 backend/tests/，所以这些断言从未在
任何流水线上执行过）。`calculator` 对 `__import__('os')` 的拦截是安全锁，
不是演示代码，现在放进 CI 覆盖范围内。

需要真起容器或真连公网的用例按缺什么 skip 什么，与 test_sandbox_concurrency.py
同一个道理：没装 Docker 的开发机不该长红，但把"没跑"记成"跑过"是假的绿。
"""
import pytest

from app.tools.executor import execute_tool

# 导入 builtin_tools 触发 @register_tool 装饰器执行，否则注册表是空的
import app.tools.builtin_tools  # noqa: F401


def test_calculator_valid():
    result = execute_tool("calculator", {"expression": "2+3"})
    assert "✓" in result
    assert "5" in result


def test_calculator_invalid_syntax():
    result = execute_tool("calculator", {"expression": "2++3"})
    assert "✗" in result


def test_calculator_forbidden():
    """净化锁：表达式里出现被禁的名字就必须被拦下，而不是"算不出但不报错"。"""
    result = execute_tool("calculator", {"expression": "__import__('os')"})
    assert "禁止" in result or "✗" in result


@pytest.mark.skip(reason="依赖公网 DDGS 且搜索结果不确定，不适合当回归锁")
def test_web_search():
    result = execute_tool("web_search", {"query": "Python"})
    assert "✓" in result
    assert "Python" in result


def test_help():
    result = execute_tool("help", {})
    assert "calculator" in result
    assert "web_search" in result
    assert "execute_code" in result


def test_unknown_tool():
    result = execute_tool("nonexistent", {})
    assert "未知" in result or "✗" in result


class _RecordingSandbox:
    """替掉真沙箱：记录每次执行请求，并按预设脚本返回结果。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def run_code(self, code, language="python", timeout=None):
        self.calls.append((code, language))
        if self.script:
            return self.script.pop(0)
        return {"stdout": "", "stderr": "", "error": "脚本已用尽"}


def test_execute_code_runs_user_code_exactly_once(monkeypatch):
    """锁住"代码只跑一遍"：曾经的重试循环写完后，又在循环外无条件执行了一次。"""
    from app.tools import builtin_tools

    fake = _RecordingSandbox([{"stdout": "hello", "stderr": "", "error": None}])
    monkeypatch.setattr(builtin_tools, "sandbox", fake)
    out = builtin_tools.execute_code("print('hello')", "python")
    assert len(fake.calls) == 1, f"用户代码被执行了 {len(fake.calls)} 遍"
    assert "hello" in out


def test_execute_code_retries_only_until_first_success(monkeypatch):
    fake = _RecordingSandbox([
        {"error": "容器启动失败"},
        {"stdout": "ok", "stderr": "", "error": None},
    ])
    from app.tools import builtin_tools
    monkeypatch.setattr(builtin_tools, "sandbox", fake)
    out = builtin_tools.execute_code("print('ok')", "python")
    assert len(fake.calls) == 2
    assert "ok" in out and "执行错误" not in out


def _sandbox_skip_reason(language):
    """沙箱跑不起来时返回 skip 原因，跑得起来返回 None。"""
    from app.sandbox.sandbox_manager import SandboxManager

    sm = SandboxManager()
    if sm.client is None:
        return f"Docker 守护进程不可用：{sm.unavailable_reason}"
    image = SandboxManager.LANGUAGE_IMAGES[language]
    try:
        sm.client.images.get(image)
    except Exception as e:
        return f"沙箱镜像 {image} 缺失（{e}）"
    return None


@pytest.fixture
def sandbox(request):
    language = request.param
    reason = _sandbox_skip_reason(language)
    if reason is not None:
        pytest.skip(f"execute_code 用例需要容器：{reason}")
    return language


@pytest.mark.parametrize("sandbox", ["python"], indirect=True)
def test_code_tool_python(sandbox):
    result = execute_tool("execute_code", {"code": "print('hello')", "language": sandbox})
    assert "hello" in result
    assert "✗" not in result


@pytest.mark.parametrize("sandbox", ["javascript"], indirect=True)
def test_code_tool_javascript(sandbox):
    result = execute_tool("execute_code", {
        "code": "console.log('hi from js');",
        "language": sandbox,
    })
    assert "hi from js" in result


@pytest.mark.parametrize("sandbox", ["python"], indirect=True)
def test_code_tool_syntax_error(sandbox):
    result = execute_tool("execute_code", {"code": "prin('typo')", "language": sandbox})
    assert "NameError" in result or "错误" in result or "error" in result


@pytest.mark.parametrize("sandbox", ["python"], indirect=True)
def test_code_tool_timeout(sandbox):
    """死循环必须被超时掐断——这是沙箱的 DoS 兜底，不是可选行为。"""
    result = execute_tool("execute_code", {"code": "while True: pass", "language": sandbox})
    assert "超时" in result or "timeout" in result or "错误" in result
