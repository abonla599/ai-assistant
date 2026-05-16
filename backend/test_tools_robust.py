from app.tools.executor import execute_tool
import app.tools.builtin_tools  # 注册

# 测试正确调用
print(execute_tool("calculator", {"expression": "2+3"}))
print(execute_tool("web_search", {"query": "Python"}))
print(execute_tool("help", {}))

# 测试错误参数
print(execute_tool("calculator", {"bad_param": "x"}))  # 应返回参数错误
print(execute_tool("unknown_tool", {}))                # 应返回未知工具，并建议使用 help