# 全局工具注册表
tools_registry = {}

def register_tool(name: str, description: str, parameters: dict):
    """
    装饰器：将函数注册为工具。
    name: 工具唯一名，如 "calculator"
    description: 工具描述，给模型看
    parameters: JSON Schema 格式的参数定义
    """
    def decorator(func):
        tools_registry[name] = {
            "function": func,
            "description": description,
            "parameters": parameters
        }
        return func
    return decorator

def get_all_tools_schema():
    """生成符合 OpenAI 工具格式的 schema 列表，用于 API 调用"""
    schema_list = []
    for name, info in tools_registry.items():
        schema_list.append({
            "type": "function",
            "function": {
                "name": name,
                "description": info["description"],
                "parameters": info["parameters"]
            }
        })
    return schema_list