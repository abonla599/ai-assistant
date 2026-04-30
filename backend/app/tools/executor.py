import json
from app.tools.registry import tools_registry

def execute_tool(tool_name: str, arguments: dict) -> str:
    """根据工具名和参数执行对应函数，返回字符串结果"""
    if tool_name not in tools_registry:
        return f"未知工具: {tool_name}"
    func = tools_registry[tool_name]["function"]
    # 注意：函数可能需要不同参数，这里统一使用关键字参数
    return func(**arguments)