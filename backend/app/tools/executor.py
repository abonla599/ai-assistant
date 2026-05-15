from app.tools.registry import tools_registry

def execute_tool(tool_name: str, arguments: dict) -> str:
    if tool_name not in tools_registry:
        return f"未知工具: {tool_name}"
    func = tools_registry[tool_name]["function"]
    try:
        result = func(**arguments)
        return str(result) if result else "[无输出]"
    except Exception as e:
        return f"[执行错误] {e}"
