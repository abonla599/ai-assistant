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


# ---------- ② 教模型发短查询（锁的是真正注册进 schema 的那句话） ----------
#
# 实测事实：把用户的整句问话（「2027年西北地区研究生招生简章什么时候发布」）原样丢给
# 源站，回来的是跟开头几个词相关的噪声；缩到 2–4 个词才有好结果。模型不知道这件事，
# 因为它看不见源站的行为，只看得见我们给它的那段描述。
#
# 所以下面这些断言一律从**给模型的清单**里读值，不读 inspect.getsource、不读整文件文本：
# 要点写在注释里而 schema 里没有，模型一个字也收不到，那时这些锁必须红。

def _web_search_function_schema(monkeypatch):
    """模型真正收到的那条 web_search schema（含 available 筛过的那一步）。"""
    from app.tools import availability
    from app.tools.registry import get_available_tools_schema

    monkeypatch.setattr(availability, "search_reachable", lambda: True)
    for tool in get_available_tools_schema():
        if tool["function"]["name"] == "web_search":
            return tool["function"]
    raise AssertionError("web_search 不在给模型的清单里，后面几条无从谈起")


def _schema_text(fn) -> str:
    """工具描述 + 每个参数的描述：模型填这条工具时能看到的全部文字。"""
    props = fn["parameters"]["properties"]
    return "\n".join([fn["description"]]
                     + [str(p.get("description", "")) for p in props.values()])


def _query_desc(fn) -> str:
    return fn["parameters"]["properties"]["query"]["description"]


# 要点一、二按"两个字段各钉一遍"来锁，不靠只查并集的那一份：并集锁会漏掉"把工具
# 描述退回『输入搜索关键词』、query 描述原封不动"这半边的退化（第一版就是这样，
# 跑变异 M1 全绿才暴露出来）。两处都写了这些要点，所以两处都能独立红。
_WORD_COUNT = r"2\s*[-~－–—至到]\s*4\s*(?:个|项)?\s*(?:短)?\s*(?:关键词|词语|词)"
_NO_WHOLE_QUESTION = r"不要[^。\n；]{0,16}(?:整句|原句|原话|完整(?:的)?(?:问题|问句)|把.{0,10}原样)"


def test_web_search_description_asks_for_a_few_keywords_not_the_whole_question(monkeypatch):
    """工具描述本身要同时写出"2-4 个词的关键词"和"不要整句"。

    只说"输入搜索关键词"是不够的：模型本来就知道关键词长什么样，它不知道的是这位
    对手**对词数敏感**——2–4 那个区间本身就是实测结论。而只有正面示范没有禁止项，
    模型照样传整句：它那句问话就在上下文里，抄起来最省事。
    """
    text = _web_search_function_schema(monkeypatch)["description"]
    assert re.search(_WORD_COUNT, text), f"工具描述里找不到词数要求：{text!r}"
    assert re.search(_NO_WHOLE_QUESTION, text), f"工具描述里找不到「不要整句」这条禁止项：{text!r}"


def test_the_query_parameter_description_carries_the_same_two_rules(monkeypatch):
    """同两条要重复在 query 参数描述里，不能只写在工具描述里。

    模型下笔填参数的那一刻看的是参数描述。只在工具描述里写一遍，等于赌它把两段话
    一起读——而工具描述只会越写越长。
    """
    text = _query_desc(_web_search_function_schema(monkeypatch))
    assert re.search(_WORD_COUNT, text), f"query 参数描述里找不到词数要求：{text!r}"
    assert re.search(_NO_WHOLE_QUESTION, text), f"query 参数描述里找不到「不要整句」：{text!r}"


def test_web_search_schema_tells_the_model_to_resolve_relative_years(monkeypatch):
    """要点三：「今年/明年/去年」要先换成具体年份——写在任一字段即可，但必须在 schema 里。

    相对时间原样送进搜索引擎，命中的一定是"说话那一年"的内容：用户问 2027 年，
    搜回来的全是 2026 年的简章，模型还会说"没找到 2027 年的"。
    """
    text = _schema_text(_web_search_function_schema(monkeypatch))
    assert re.search(r"(?:今年|明年|去年|前年)[^。\n；]{0,40}(?:换算|换成|改写成|替换成|具体年份)", text) \
        or re.search(r"(?:换算|换成|改写成|具体年份)[^。\n；]{0,40}(?:今年|明年|去年)", text), \
        f"schema 里没教模型把相对时间换成具体年份：{text!r}"


def test_web_search_availability_is_still_resolved_at_call_time(monkeypatch):
    """顺手钉住这行没被改坏：`available=lambda: availability.search_reachable()`。

    改 description 时最容易顺手"整理"同一块装饰器。两种写法都会红在这里：
    - `from app.tools.availability import search_reachable` 再直接交函数：抓的是
      导入那一刻的函数对象，源层换实现/测试打桩都不跟着动；
    - `available=availability.search_reachable()`：导入期求值定死一个布尔值，之后
      源站坏了工具还挂在清单里（那个 bool 不可调用，is_available 里抛错又被
      fail-open 吞成"可用"，症状正是本项目防的坏源一直递给模型）。

    既有锁在 test_tool_availability.py::test_web_search_follows_the_probe，这里是
    第二次改动现场的复核，不是替身。
    """
    from app.tools import availability
    from app.tools.registry import get_available_tools_schema, tools_registry

    monkeypatch.setattr(availability, "search_reachable", lambda: False)
    assert "web_search" not in {t["function"]["name"] for t in get_available_tools_schema()}, \
        "源站坏了 web_search 还在清单里：available 不再是调用期读的模块属性"
    monkeypatch.setattr(availability, "search_reachable", lambda: True)
    assert "web_search" in {t["function"]["name"] for t in get_available_tools_schema()}
    assert callable(tools_registry["web_search"]["available"]), \
        "注册进表的必须是可调用对象，不是导入期算出来的布尔值"


# ---------- ③ 相关性守卫：有结果，但结果可能与问题无关 ----------
#
# 空结果那句（"不要据此断定不存在"）已经在了；有结果却全是噪声时，模型会照着不相干的
# 标题编答案，而这是更难发现的一种错法。守卫只在**零交集**时追加一句：部分重叠就不加，
# 否则每次正常结果后面都挂一句疑神疑鬼的话，模型学会的是忽略它。

_IRRELEVANT_MARK = "可能与你的问题无关"


def _results_page(titles) -> str:
    """造一页有机结果，标题可控。块内那串 `<link>` 垃圾留着，形状照真实响应。"""
    block = ('<li class="b_algo"><link rel="stylesheet" href="/rp/a.css"/>'
             '<h2><a href="https://ex.example/{i}">{title}</a></h2>'
             '<div class="b_caption"><p>摘要 {i}</p></div></li>')
    return ("<html><body><ol>"
            + "".join(block.format(i=i, title=t) for i, t in enumerate(titles))
            + "</ol></body></html>")


def _serve_page(monkeypatch, titles):
    """让这一趟搜索拿到上面那页假结果：出站请求交给 MockTransport，不打真源站。

    `note_search_ok` 一起钉成空操作——它是进程级全局（可用性缓存的状态与时间戳），
    带着出用例就是下一条用例的"源刚刚好用过"是上一条评论留下的。
    """
    import httpx

    from app.tools import availability

    html = _results_page(titles)
    monkeypatch.setattr(availability, "note_search_ok", lambda: None)

    class _Factory:
        def __init__(self):
            self._real = httpx.Client      # 换掉之前抓住真身，否则 __call__ 里自递归

        def __call__(self, **kwargs):
            kwargs["transport"] = httpx.MockTransport(
                lambda request: httpx.Response(200, text=html))
            return self._real(**kwargs)

    monkeypatch.setattr(httpx, "Client", _Factory())


def test_results_sharing_nothing_with_the_query_get_one_warning_line(monkeypatch):
    """零交集 → 追加一句明确提示，并且**结果照旧返回**。

    反着改会红的两种写法：①只回那句提示、把行丢掉（用户看见的就是"搜不到"）；
    ②那句提示含糊到模型照样照抄噪声标题，所以断言同时要求"换个关键词再搜"这个出口。
    """
    _serve_page(monkeypatch, ["西安市天气查询", "今日油价上涨了吗"])
    out = execute_tool("web_search", {"query": "西北地区研究生招生简章"})
    assert _IRRELEVANT_MARK in out, f"完全无关的结果没有触发提示：{out!r}"
    assert "换个关键词" in out, f"提示里没给出下一步：{out!r}"
    assert "西安市天气查询" in out and "今日油价上涨了吗" in out, \
        f"加了提示却把结果吞了：{out!r}"
    assert out.count("https://ex.example/") == 2, f"结果行少了：{out!r}"


def test_relevant_results_get_no_warning_even_for_a_spaceless_chinese_question(monkeypatch):
    """中文问句不许被误判为无关——这条专打"按空格分词"的写法。

    中文查询没有空格：拿 `query.split()` 切出来就是一个 giant token，它当然不在任何
    标题里，于是守卫**永远**判无关、永远挂那句提示，正常结果全被污染。所以判交集
    必须落到字符/子串上。这里连标题都是真相关的一条，仍然挂提示就是错的。
    """
    _serve_page(monkeypatch, ["2026年西北地区研究生招生简章-中国研究生招生信息网"])
    out = execute_tool("web_search", {"query": "西北地区研究生招生简章什么时候发布"})
    assert _IRRELEVANT_MARK not in out, f"中文问句被误判为无关：{out!r}"
    assert "研究生招生信息网" in out, out


def test_any_partial_overlap_switches_the_warning_off(monkeypatch):
    """保守判据：只要有一个实词对得上就不挂那句。

    谁把判据改成"重叠得不够多就算无关"，这条就红——那是把正常结果天天泡在提示里，
    而一句天天出现的提示等于没有提示。
    """
    _serve_page(monkeypatch, ["完全不搭边的另一件事", "简章里的三条注意事项"])
    out = execute_tool("web_search", {"query": "西北 研究生 招生简章"})
    assert _IRRELEVANT_MARK not in out, f"只有一个词重叠就挂了提示：{out!r}"


def test_the_guard_does_not_fire_on_the_empty_result_answer(monkeypatch):
    """空结果走原来那句，不叠加守卫。

    判据放到 `if not rows` 之前，模型就会同时收到"没有返回结果"和"以下结果可能与你的
    问题无关"两句自相矛盾的话。
    """
    from app.tools import web_search as ws

    monkeypatch.setattr(ws, "search", lambda query, max_results=3: [])
    out = execute_tool("web_search", {"query": "西北地区研究生招生简章"})
    assert _IRRELEVANT_MARK not in out, f"空结果被套上了有结果的提示：{out!r}"
    assert "没有返回结果" in out and "不要据此断定" in out, out


def test_a_page_that_only_shares_the_year_is_still_irrelevant(monkeypatch):
    """只共享年份与散字的噪声页必须触发守卫——这就是真实取证里那一页的样子。

    2026-09-22 拿那句长问句打真源站，回来的是「2027年_百度百科」「2027日历表」
    「2027年放假安排」。它们和查询只共享「2027」和「年」「西」「北」这类散字。
    单字一旦算进交集，这一页就被判"相关"，守卫恰好在最该它开口的那次不吭声。
    """
    _serve_page(monkeypatch, ["2027年_百度百科", "2027日历表：农历、节气与周末",
                              "2027年中国放假安排（暂定）"])
    out = execute_tool("web_search", {"query": "2027年西北地区研究生招生简章什么时候发布"})
    assert _IRRELEVANT_MARK in out, f"只共享年份的噪声页被判成相关：{out!r}"


def test_query_terms_split_subject_from_year_and_ignore_whitespace():
    """判交集用的"实词"怎么切：主题词与年份分两块，标点虚词不算。

    这条直接看切出来的词，是为了让"按空格切"这种实现无处藏身：下面这个问句一个空格
    都没有，`split()` 只会给出一个元素，那条 e2e 锁就会永远判无关。
    """
    from app.tools import builtin_tools

    subject, qualifier = builtin_tools._query_terms("西北地区研究生招生简章")
    assert len(subject) >= 4, f"整句问话被当成一个词了（多半是按空格切的）：{subject}"
    assert {"招生", "简章", "西北"} <= subject, subject
    assert "西北地区研究生招生简章" not in subject, subject
    # 单字不成片：散字到处都有，认它等于任何两条中文都"相关"。
    assert not any(len(t) == 1 for t in subject), f"混进了单字，守卫将永远不触发：{subject}"
    assert qualifier == set(), f"没有数字的问句凭空多出限定词：{qualifier}"

    subject, qualifier = builtin_tools._query_terms("2027年 招生简章")
    assert {"招生", "简章"} <= subject and qualifier == {"2027"}, \
        "年份要单独放一块：取证那次它就是靠「2027」混进交集的"
    subject, qualifier = builtin_tools._query_terms("他的项目是在什么时候完结的呀")
    joined = "".join(subject | qualifier)
    for particle in "的了是在呀":       # 纯虚词：一律不许进到 token 里
        assert particle not in joined, f"虚词被当成实词了：{joined}"
    subject, _ = builtin_tools._query_terms("他是什么时候完结的")
    assert "什么" not in subject and "时候" not in subject, \
        f"疑问词组被当成了实词，与任何页面都对不上：{subject}"


def test_a_year_only_query_uses_the_year_as_its_subject():
    """整句只剩年份时，年份就是主题——不许因为它只是"限定词"就永远不吭声。

    把 `if not subject: subject = qualifier` 那一行删掉，「2027」这种查询切出来主题词
    为空，守卫对任何一页都回"相关"，那条零交集的锁在这类查询上整个失效。
    """
    from app.tools import builtin_tools

    rows = [{"title": "2027年放假安排", "url": "https://e/1", "snippet": "暂定"}]
    assert builtin_tools._rows_share_a_word("2027", rows) is True
    other = [{"title": "今年的放假安排", "url": "https://e/2", "snippet": "暂定"}]
    assert builtin_tools._rows_share_a_word("2027", other) is False, \
        "只问年份、结果里连年份都没有，却被判成相关"


def test_punctuation_never_becomes_a_term():
    """标点不算实词，而且不许被切进 token。

    汉字区间一旦写成 `[\\u3400-\\u9fff]`（下界往下扩），就会顺手把 CJK 符号与标点区
    U+3000–303F 圈进来——「。」「、」正是招生简章标题里到处都在用的那批字符，于是
    「。」「、吗」这类 token 会一路混进交集判断。下面这个断言专抓那一种区间。
    """
    from app.tools import builtin_tools

    subject, qualifier = builtin_tools._query_terms("西北。招生简章、研究生：2027")
    assert {"西北", "招生", "简章"} <= subject and qualifier == {"2027"}
    for term in subject | qualifier:
        assert not any(c in "。，、：；！？（）《》「」…—·" for c in term), \
            f"标点被切进了 token：{term!r}"


def test_a_query_of_only_particles_gets_no_verdict(monkeypatch):
    """无从判断时必须不挂那句（宁可漏报）。

    去掉虚词表之后，「他的了是在」这种查询会切出一堆噪声 token，与任何标题都对不上，
    于是每问一句"是什么呢"都白挂一句提示。
    """
    from app.tools import builtin_tools

    assert builtin_tools._query_terms("他的了是在，。！、吗") == (set(), set())
    rows = [{"title": "某件毫不相干的事", "url": "https://e/1", "snippet": "随便一句"}]
    assert builtin_tools._rows_share_a_word("他的了是在，。！、吗", rows) is True, \
        "没有实词可比，却被判成无关"
