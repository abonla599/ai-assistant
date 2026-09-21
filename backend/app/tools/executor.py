from app.tools.registry import tools_registry
from app.tools.response import ToolResponse

def execute_tool(tool_name: str, arguments: dict, user_id: str = None) -> str:
    """执行一条工具。

    `user_id` 由调用方从**凭据**里算出来传进来（两条聊天路径都是这么做的），
    它不进模型可见的参数表：标了 needs_user 的工具，这里用服务端那份覆盖掉
    模型可能传上来的同名参数。方向反过来（让模型说了算）就是"读谁的日程"由模型编。
    """
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