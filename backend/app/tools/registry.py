# 临时注册器，模拟费丙乾的工具注册模块
tools_registry = {}

def register_tool(name: str, description: str, parameters: dict):
    def decorator(func):
        tools_registry[name] = {
            "function": func,
            "description": description,
            "parameters": parameters
        }
        return func
    return decorator

def get_all_tools_schema():
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
