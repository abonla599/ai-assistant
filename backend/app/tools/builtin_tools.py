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
from app.tools import web_search as search_source
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

# ---------- 联网搜索工具 ----------
# 取证（2026-09 实测）：把用户那句问题原样丢给源站，回来的只是跟开头几个词相关的
# 噪声；查询缩到 2–4 个词才有好结果。模型看不见源站的行为，只能看见下面这两段
# 描述——所以"怎么问"这件事必须写进 schema，写在注释里它一个字也收不到。
@register_tool(
    name="web_search",
    description="搜索互联网获取实时信息，返回几条结果的标题、摘要与来源网址。"
                "query 请写成 2-4 个词的关键词（如「西北大学 2027 招生简章」），"
                "不要把用户的整句问题原样传进来：词一多，回来的就只是跟开头几个词相关的噪声。"
                "问题里有「今年/明年/去年」这类相对时间时，先换算成具体年份再搜。",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词：2-4 个词、用空格隔开，不要把整句话原样传进来；"
                               "「今年/明年/去年」这类相对时间先换成具体年份（如 2027）"
            }
        },
        "required": ["query"]
    },
    # 这里必须是 availability 模块属性调用（而不是 from ... import search_reachable），
    # 也不能在导入期求值：源站通不通是后台每 15 分钟真跑一次查询测出来的，
    # 导入期定死就退化成名单。
    available=lambda: availability.search_reachable()
)
def web_search_tool(query: str) -> str:
    """一次真实搜索。永远回一句人话：不回异常，也不回假结果。

    空结果那句是特意写成"不要据此断定不存在"的：模型拿到一句"没找到"就顺手回答
    "这事不存在"，是这类工具最贵的错法——搜不到与不存在是两回事。
    有结果但全是噪声是另一半错法：它会照着不相干的标题编，而那次它是有底气的。
    """
    rows = search_source.search(query)
    if not rows:
        return ("这次搜索没有返回结果。可能是源站暂时不给，也可能是关键词的问题；"
                "不要据此断定这件事不存在，可以换个说法再搜一次，或者如实说查不到。")
    # 真人搜成功一次 = 源此刻好用，这是比定时探测更强的证据，也让探测别再敲源站
    availability.note_search_ok()
    body = "\n".join(f"- {r['title']}: {r['snippet']} （来源 {r['url']}）" for r in rows)
    if not _rows_share_a_word(query, rows):
        # 追加而不替换：行一条都不许少，用户要核对时靠的还是那些 URL。
        body += ("\n〔提示〕以上结果里没有一条包含你这句查询的关键词，"
                 "它们可能与你的问题无关：不要据此编造答案，"
                 "换个关键词（更少、更具体的词）再搜一次。")
    return body


# 虚词与标点。它们不进"实词"：页面上到处都是「的/是/了」，认它们等于任何两条中文
# 都有交集，守卫就永远不触发；反过来要是整句照抄去做子串匹配，一句问话永远匹配不
# 上任何标题，守卫就永远触发——两头都是废掉这条判据，所以必须按词切。
# 疑问词（什么/怎么/时候…）走 _STOP_TERMS 而不是按单字砍：`时` 在「考试时间」里、
# `候` 在「候选人」里都是实义成分，一起砍就砍错地方了。
_STOP_CHARS = set("的了着是在和与及或也就而被把从到于之这那个们吗呢吧啊呀哦嗯请问你我他她它您谁")
# 只有"成词"才算虚词的：从切出来的 token 里丢掉，不作为交集的证据。
_STOP_TERMS = {"什么", "时候", "怎么", "怎样", "如何", "多少", "多久", "为什么", "哪些",
               "哪个", "那个", "这个", "可以", "是不", "有没有"}

# 词内相邻两字成 token：整句问话于是裂成一片可比较的小片段，交集判断才有牙。
# 汉字段取统一汉字 U+4E00–9FFF 加扩展 A U+3400–4DBF（生僻姓名/地名落在扩展 A 那一档）。
# 中文标点一律在这两段之外（、。《》「」在 U+3000–303F，，！？（）在 U+FF00 段），所以
# 天然进不了 token——这是判据成立的前提：标点一旦混进来，「。」会和任何带句号的标题
# "对上"，守卫就永远不触发。哪天有人把下界一路扩到 U+3000，
# test_punctuation_never_becomes_a_term 会红在那里。
_WORD_RUNS = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff\u3400-\u4dbf]+")
# 虚词按"切开"而不是"整串砍掉"：砍在一串汉字的中间会把「研究」切成「究」。
_SPLIT_STOP = re.compile("[" + "".join(sorted(_STOP_CHARS)) + "]")


def _query_terms(query: str):
    """把查询切成两块：主题词（中文两字片段与字母词）与限定词（纯数字串）。

    **单字不算主题词**：2026-09-22 拿真实源站量过——问「2027年西北地区研究生招生简章
    什么时候发布」回来的是「2027日历表」「2027年放假安排」这种噪声页，它和查询只共享
    「2027」与一两个散字。单字一旦算进交集，这种页就被判"相关"，守卫在最该它说话的
    那次正好不吭声。
    **年份只做限定词，不做主题词**：上面那页正是靠「2027」混进交集的。但整句查询只剩
    年份时（「2027」单问），年份就是全部主题，所以分两块交出去由调用方定夺。
    **绝不按空格分词**：中文问句里没有空格，`query.split()` 会得到一个 giant token，
    它与任何标题都匹配不上，于是这条守卫对每一句中文都判"无关"——那是比没有守卫
    更糟的形状（每问一句都挂一句疑神疑鬼的话）。
    """
    subject, qualifier = set(), set()
    for run in _WORD_RUNS.findall(str(query or "").lower()):
        if run[0].isdigit():
            qualifier.add(run)
            continue
        if run[0].isascii() and run.isascii():
            subject.add(run)                        # 字母词整词就是主题词，不切两字
            continue
        for segment in _SPLIT_STOP.split(run):
            subject.update(segment[i:i + 2] for i in range(len(segment) - 1))
    return subject - _STOP_TERMS, qualifier


def _rows_share_a_word(query: str, rows: list) -> bool:
    """返回的标题+摘要里是否找得到一个查询主题词。判据是**子串**，不是整词相等。

    取"零交集才报警"这个最保守的形状：有一点点重叠就不吭声。误报的代价是模型天天
    被一句无关提示干扰、最后连真提示一起忽略；漏报只是回到今天的行为。
    唯一不算判断的判断是切不出主题词（整句查询都是虚词与标点）——那时回"相关"。
    某条结果缺摘要只是少一份可比文本，不另开分支：解析层本来就要求有标题才成一条。
    """
    subject, qualifier = _query_terms(query)
    if not subject:
        subject = qualifier                         # 只问了个年份时，年份就是主题
    if not subject:
        return True
    text = " ".join(f"{r.get('title', '')} {r.get('snippet', '')}" for r in rows).lower()
    return any(term in text for term in subject)

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


# ---------- 日程：今天该干什么 ----------
# 两条都标 needs_user，理由是同一句：日程是"某个具体的人"的数据，归属人只能由执行器
# 从凭据里注入。参数表里刻意不出现 user_id——多一个这样的口子，就等于请模型编一个归属人。
from app.core import schedule as _schedule


@register_tool(
    name="today_plan",
    description="读这个人今天的日程清单（事项、时间、做完没有）。返回值第一行是服务端的今天"
                "与星期几。问「今天该干什么」「还有什么没做」之前先调它，别凭对话里的印象猜；"
                "要把「明天」「下周三」换算成日期，也以它给的那天为基准。",
    parameters={"type": "object", "properties": {}},
    needs_user=True,
)
def today_plan(user_id: str):
    return _schedule.render_for_model(user_id)


@register_tool(
    name="plan_add",
    description="往这个人的日程里追加一条。他说「记得提醒我交周报」「明天下午三点开会」时调用。"
                "day 省略就是今天；要写别的日子，先调 today_plan 拿到今天再换算，别凭印象猜日期。"
                "at 只收 24 小时的 HH:MM，不知道几点就别填。",
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "一句话事项，不超过 200 字"},
            "at": {"type": "string", "description": "时间 HH:MM，例如 15:00；省略=不定点"},
            "day": {"type": "string", "description": "YYYY-MM-DD，省略=今天"},
        },
        "required": ["text"],
    },
    needs_user=True,
)
def plan_add(user_id: str, text: str, at: str = "", day: str = ""):
    item = _schedule.add_item(user_id, text, at=at, day=day)
    return {"day": _schedule.normalize_day(day), "text": item["text"],
            "at": item["at"], "id": item["id"]}