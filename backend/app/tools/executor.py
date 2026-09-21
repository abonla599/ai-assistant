from app.tools.registry import tools_registry
from app.tools.response import ToolResponse

# 一次工具调用允许发给模型多少字。
#
# 为什么要裁：execute_code 打一万行日志、web_search 回一整页摘要，这些都是**本轮已经
# 付过钱**的内容——超限部分不是"省下来了"，是白付。更实在的代价在后面：上下文窗口是
# 满的，塞进来的工具输出会把同一轮里更早的对话挤出去，用户看到的症状是"它忘了我说过
# 的话"。所以这道闸放在执行器出口这一个地方：两条聊天路径加 ReAct 那条都从这儿过，
# 以后新加的调用方不可能忘（本项目已经因"只有一路径带了 tools"吃过一次产品级缺口）。
TOOL_OUTPUT_BUDGET = 4000


def _omission(omitted: int, total: int, budget: int) -> str:
    return (f"…〔中段已省略 {omitted} 字：工具输出共 {total} 字，超出给模型的 "
            f"{budget} 字预算，这里只保留开头与结尾〕…")


def clip_for_model(text: str, budget: int = TOOL_OUTPUT_BUDGET) -> str:
    """按预算截断，并在文本里写明截了多少。

    静默截断比截断本身更糟：模型会以为它看到的是全貌，然后理直气壮地基于半截日志
    下结论。所以省略号里带字数，让它有机会说"输出被截断了"。
    头 2/3 尾 1/3 —— 说明与参数在开头，而报错与结果的关键行几乎总在结尾。
    """
    total = len(text)
    if total <= budget:
        return text
    # 先按"最坏长度的那句省略说明"（两个数字都按 total 的位数）预留位置，再拿实际
    # 长度拼一次，于是 头+说明+尾 ≤ 预算 是可证的，不需要事后修剪。
    room = max(budget - len(_omission(total, total, budget)), 0)
    tail = room // 3
    head = room - tail
    return text[:head] + _omission(total - room, total, budget) + (text[total - tail:] if tail else "")


def execute_tool(tool_name: str, arguments: dict, user_id: str = None) -> str:
    """执行一条工具，返回**已在上下文预算内**的结果文本。

    `user_id` 由调用方从**凭据**里算出来传进来（两条聊天路径都是这么做的），它不进
    模型可见的参数表：标了 needs_user 的工具，执行器用服务端那份覆盖掉模型可能传上
    来的同名参数。方向反过来（让模型说了算）就是"读谁的日程"由模型编。
    """
    raw = _dispatch(tool_name, arguments, user_id)
    clipped = clip_for_model(raw)
    if len(clipped) != len(raw):
        print(f"[Tools] {tool_name} 输出 {len(raw)} 字，超出 {TOOL_OUTPUT_BUDGET} 字预算，"
              f"已截断后再发给模型")
    return clipped


def _dispatch(tool_name: str, arguments: dict, user_id: str = None) -> str:
    if tool_name not in tools_registry:
        return ToolResponse(False, error=f"未知工具: {tool_name}", hint="使用 help 工具查看可用工具列表").to_string()
    info = tools_registry[tool_name]
    func = info["function"]
    kwargs = dict(arguments)
    if info.get("needs_user"):
        if not (user_id or "").strip():
            return ToolResponse(False, error="缺少身份：这条工具只查得到某个具体人的数据",
                                hint="请从已登录的会话里调用").to_string()
        kwargs["user_id"] = user_id
    try:
        result = func(**kwargs)
        # 如果函数本身返回 ToolResponse，则保持，否则包装
        if isinstance(result, ToolResponse):
            return result.to_string()
        return ToolResponse(True, data=result).to_string()
    except TypeError as e:
        return ToolResponse(False, error=f"参数错误: {e}", hint="请检查工具参数是否正确").to_string()
    except Exception as e:
        return ToolResponse(False, error=str(e), hint="重试或使用其他方法").to_string()