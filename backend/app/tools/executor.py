from app.tools.registry import tools_registry
from app.tools.response import ToolResponse

def execute_tool(tool_name: str, arguments: dict) -> str:
    if tool_name not in tools_registry:
        return ToolResponse(False, error=f"未知工具: {tool_name}", hint="使用 help 工具查看可用工具列表").to_string()
    func = tools_registry[tool_name]["function"]
    try:
        result = func(**arguments)
        # 如果函数本身返回 ToolResponse，则保持，否则包装
        if isinstance(result, ToolResponse):
            return result.to_string()
        return ToolResponse(True, data=result).to_string()
    except TypeError as e:
        return ToolResponse(False, error=f"参数错误: {e}", hint="请检查工具参数是否正确").to_string()
    except Exception as e:
        return ToolResponse(False, error=str(e), hint="重试或使用其他方法").to_string()