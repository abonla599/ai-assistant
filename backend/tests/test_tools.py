"""工具分发与表达式净化的回归锁。

本文件原先躺在 backend/ 根目录（CI 只跑 backend/tests/，所以这些断言从未在
任何流水线上执行过）。`calculator` 对 `__import__('os')` 的拦截是安全锁，
不是演示代码，现在放进 CI 覆盖范围内。

需要真起容器或真连公网的用例按缺什么 skip 什么，与 test_sandbox_concurrency.py
同一个道理：没装 Docker 的开发机不该长红，但把"没跑"记成"跑过"是假的绿。
"""
import pytest

import re
from pathlib import Path

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


def test_calculator_does_not_eval_the_expression():
    """结构锁：表达式不许交给 eval。

    黑名单是按子串砍的（`__`/`os`/`sys`…），而解释器看到的却是整条字符串——这种
    "过滤与执行不对称"的写法每加一个合法需求就要再赌一次。AST 白名单把赌局取消：
    不认识的节点一律拒，默认拒绝而不是默认允许。
    """
    import ast as _ast
    import inspect

    from app.tools import builtin_tools

    src = inspect.getsource(builtin_tools.calculator)
    used = {n.func.id for n in _ast.walk(_ast.parse(src))
            if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Name)}
    assert "eval" not in used and "exec" not in used, "calculator 又回到 eval 了"


def test_calculator_supports_power_operator():
    """`2**10` 是数学，不是危险字符。旧实现那条"禁止连续运算符"的正则把它砍了。"""
    result = execute_tool("calculator", {"expression": "2**10"})
    assert "✓" in result, result
    assert "1024" in result


def test_calculator_allows_math_names_that_happen_to_contain_blocked_substrings():
    """`math.cos` 里含 "os"，于是被子串黑名单误杀——而 hint 明说支持 math 函数。

    白名单按"节点是什么"判断，就不会因为一个函数名里恰好有两个字母而说谎。
    """
    result = execute_tool("calculator", {"expression": "math.cos(0)"})
    assert "✓" in result, result
    assert "1.0" in result


def test_calculator_refuses_work_that_would_blow_up_the_context():
    """`math.factorial(200000)` 能算，但结果约 98 万位数字，会整块塞进模型上下文。

    限制必须落在**求值之前**（看参数大小），不能算完再嫌大——那样 CPU 已经付过了。
    今天这条是红的，但不是因为没拦住：CPython 3.11 的 int→str 4300 位上限在
    `str(result)` 时替我们抛了 ValueError，属于"意外正确"。所以断言要求一句
    点名上限的拒绝，而不是任何一句报错。
    """
    result = execute_tool("calculator", {"expression": "math.factorial(200000)"})
    assert "✗" in result, f"超大参数没有被拒绝：{result[:80]}"
    assert "上限" in result or "过大" in result, f"拒绝的理由不是参数上限：{result[:80]}"
    assert len(result) < 500, "结果本身变成了上下文炸弹"


def test_help_lists_the_tools_that_can_run(monkeypatch):
    """help 的清单从 2026-09-20 起按实测可用性裁剪（见 test_tool_availability.py），
    所以这里不能只读结果就断言"三个都在"——本机没 Docker 时它本来就只剩两个，
    那是正确行为，不是回归。把两个外部依赖钉成"通"，这条测的才是它原本想测的东西：
    每一行是 `名字: 用途`。
    """
    from app.tools import availability, builtin_tools
    from types import SimpleNamespace

    monkeypatch.setattr(availability, "search_reachable", lambda: True)
    monkeypatch.setattr(builtin_tools, "sandbox", SimpleNamespace(client=object()))
    result = execute_tool("help", {})
    for name in ("calculator", "web_search", "execute_code"):
        assert f"{name}:" in result, f"help 少了 {name} 这一行：{result!r}"


def test_unknown_tool():
    result = execute_tool("nonexistent", {})
    assert "未知" in result or "✗" in result


def test_wrong_argument_name_is_reported_not_raised():
    """参数名传错走的是 TypeError 分支：必须变成一句可读的"参数错误"。

    模型生成的 tool call 经常拼错参数名，这条断言保证它不会把整个回合带崩。
    """
    result = execute_tool("calculator", {"bad_param": "x"})
    assert "参数错误" in result


class _RecordingSandbox:
    """替掉真沙箱：记录每次执行请求，并按预设脚本返回结果。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        # 真 SandboxManager 有 client，可用性检查读的就是它。假身少了这个字段，
        # 任何走 get_available_tools_schema() 的用例都会撞到 AttributeError 再 fail-open。
        self.client = object()

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


def test_code_tool_python(sandbox_language):
    sandbox_language("python")
    result = execute_tool("execute_code", {"code": "print('hello')", "language": "python"})
    assert "hello" in result
    assert "✗" not in result


def test_code_tool_javascript(sandbox_language):
    # CI 只构建 python 那个镜像，所以这条在 CI 上是 skip；谁建了
    # ai-sandbox-node:latest，它就在谁那里真跑。
    sandbox_language("javascript")
    result = execute_tool("execute_code", {
        "code": "console.log('hi from js');",
        "language": "javascript",
    })
    assert "hi from js" in result


def test_code_tool_syntax_error(sandbox_language):
    sandbox_language("python")
    result = execute_tool("execute_code", {"code": "prin('typo')", "language": "python"})
    assert "NameError" in result or "错误" in result or "error" in result


def test_code_tool_timeout_reports_timeout_to_the_model(monkeypatch):
    """只验"超时有没有用人话交出去"；真沙箱掐死循环由 test_sandbox.py 锁。

    这里走真容器的话，execute_code 的 max_retries=2 会把它拖成三次 10 秒超时。
    """
    from app.tools import builtin_tools

    fake = _RecordingSandbox([{"error": "代码执行超时（3秒）"}] * 3)
    monkeypatch.setattr(builtin_tools, "sandbox", fake)
    result = builtin_tools.execute_code("while True: pass", "python")
    assert "超时" in result


def test_nonzero_exit_code_is_surfaced_not_wrapped_as_success(monkeypatch):
    """CI 上的真实事故形状：容器读不到代码文件，python 打印 Errno 13 后非零退出，
    而 error 字段是 None——旧实现会把它包成 "✓ 输出:" 交给模型，等于谎报成功。
    """
    from app.tools import builtin_tools

    fake = _RecordingSandbox([{
        "stdout": "python: can't open file '/tmp/code.py': [Errno 13] Permission denied\n",
        "stderr": "", "error": None, "exit_code": 2,
    }])
    monkeypatch.setattr(builtin_tools, "sandbox", fake)
    result = builtin_tools.execute_code("print(1)", "python")
    assert "退出码: 2" in result, "非零退出码被吞掉了，模型会以为代码跑成功了"


def test_zero_exit_code_adds_no_noise(monkeypatch):
    from app.tools import builtin_tools

    fake = _RecordingSandbox([{"stdout": "hi\n", "stderr": "", "error": None, "exit_code": 0}])
    monkeypatch.setattr(builtin_tools, "sandbox", fake)
    result = builtin_tools.execute_code("print('hi')", "python")
    assert "退出码" not in result
    assert "hi" in result


# ---------- 智能体那条路不许有第二套工具 ----------

def test_the_agent_path_has_no_second_tool_registry():
    """`agents/` 里不许再养一套平行的工具。

    这里原先是 `agents/temp_tools.py`：calculator 用裸 `eval`（同一件事在
    builtin_tools 里已经修过一遍，第二份就把那个修复绕回去了），web_search 是一张
    写死的问答表——"马斯克""火箭回收"命中就返回背好的句子——而它的 schema 对模型
    写着"搜索互联网获取信息"。模型于是把自己的幻觉当成检索结果引用进回答，
    全程没有任何报错。这正是本项目最贵的那类失败：效果没了，还一声不响。
    """
    root = Path(__file__).resolve().parent.parent
    agents = root / "app" / "agents"
    assert not (agents / "temp_tools.py").exists(), "平行工具表又回来了"

    src = (agents / "executor.py").read_text(encoding="utf-8")
    assert "from app.tools.registry import" in src, "Executor 不再取全局注册表"
    assert "get_available_tools_schema" in src, \
        "取的是全量清单而不是按可用性筛过的那份：没 Docker 时 execute_code 又会出现在模型眼前"

    for name in ("task_agent.py", "react_agent.py", "executor.py", "orchestrator.py"):
        body = (agents / name).read_text(encoding="utf-8")
        assert not re.search(r"\beval\(", body), f"{name} 里出现了 eval"
    task = (agents / "task_agent.py").read_text(encoding="utf-8")
    assert "execute_tool(tool_name, tool_args, user_id=self.user_id)" in task, (
        "TaskAgent 又绕过执行器直接 call 注册表里的函数：needs_user 与输出预算同时失效")


def test_only_a_search_that_actually_returned_something_ages_the_probe(monkeypatch):
    """真搜出结果才告诉探测"别再敲源站"；空结果不算成功。

    反过来的代价不是报错，是把一个坏源永远钉在清单上：每次搜出空 → note_search_ok →
    探测跳过 → 工具一直递给模型 → 用户每次得到"没找到"。所以这一条要能抓住
    "在 return 之前无条件 note" 的写法。
    """
    from app.tools import availability, builtin_tools, web_search as ws   # noqa: F401

    notes = []
    monkeypatch.setattr(availability, "note_search_ok", lambda: notes.append(1))
    monkeypatch.setattr(ws, "search", lambda query, max_results=3: [])
    execute_tool("web_search", {"query": "x"})
    assert notes == [], "空结果也刷新了可用性：坏源会被自己的工具判成好用"

    monkeypatch.setattr(ws, "search",
                        lambda query, max_results=3: [{"title": "标题",
                                                        "url": "https://e/1", "snippet": "摘要"}])
    out = execute_tool("web_search", {"query": "x"})
    assert notes == [1], f"搜成功却没告诉探测，探测会照旧每 15 分钟空敲：{out[:60]}"


def test_the_search_result_carries_the_source_url_to_the_model(monkeypatch):
    """URL 必须进工具输出：模型引用来源、用户在手机上想点开原文，靠的都是它。

    只给标题与摘要的搜索结果没法核对，等于把"信不信由我"塞回模型。
    """
    from app.tools import web_search as ws

    monkeypatch.setattr(ws, "search",
                        lambda query, max_results=3: [{"title": "某校招生简章",
                                                        "url": "https://example.edu/zsjz",
                                                        "snippet": "2026 年计划……"}])
    out = execute_tool("web_search", {"query": "某校 招生"})
    assert "https://example.edu/zsjz" in out, out
    assert "某校招生简章" in out and "2026 年计划" in out, out
