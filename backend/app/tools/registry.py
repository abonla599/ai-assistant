# 全局工具注册表
tools_registry = {}

def register_tool(name: str, description: str, parameters: dict, available=None,
                  needs_user: bool = False):
    """
    装饰器：将函数注册为工具。
    name: 工具唯一名，如 "calculator"
    description: 工具描述，给模型看
    parameters: JSON Schema 格式的参数定义
    needs_user: 这条工具读写的是"某个具体的人"的数据。标了它，执行器会把**服务端算出来的**
               当前登录者塞进去，模型给的同名参数一律作废——否则 schema 里多一个 user_id，
               就等于请模型编一个归属人。
    available: 可选，无参可调用，返回这个工具"现在能不能真跑成"。
               依赖外部条件的工具（要 Docker、要够得着某个搜索源）必须给出：
               否则模型每次都会看见它、调用它、拿回一句失败，再凭失败硬答。
    """
    def decorator(func):
        tools_registry[name] = {
            "function": func,
            "description": description,
            "parameters": parameters,
            "available": available,
            "needs_user": needs_user,
        }
        return func
    return decorator

def is_available(info: dict) -> bool:
    """这个工具现在能不能用。检查本身出错时按"能用"处理。

    方向要选对：误开只是多一次失败调用，误关是把一个能用的功能悄悄藏起来——
    前者用户会抱怨，后者没人会去查，所以宁可误开。
    """
    check = info.get("available")
    if check is None:
        return True
    try:
        return bool(check())
    except Exception as e:
        print(f"⚠️ 工具可用性检查失败，按可用处理：{e}")
        return True

def _schema_of(name: str, info: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": info["description"],
            "parameters": info["parameters"]
        }
    }

def get_all_tools_schema():
    """生成符合 OpenAI 工具格式的 schema 列表，用于 API 调用。

    注意：这是**全量**清单，不看可用性。给模型的一律用
    get_available_tools_schema()，这份留给运维和"到底注册了什么"的核对。
    """
    return [_schema_of(name, info) for name, info in tools_registry.items()]

def get_available_tools_schema():
    """给模型的清单：只含现在真能跑成的那几个。"""
    return [_schema_of(name, info) for name, info in tools_registry.items()
            if is_available(info)]
