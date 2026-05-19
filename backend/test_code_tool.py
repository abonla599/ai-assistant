from app.tools.executor import execute_tool
import app.tools.builtin_tools  # 确保所有工具被注册

# 测试1：执行 Python 代码，打印 hello
res1 = execute_tool("execute_code", {"code": "print('hello')", "language": "python"})
print("测试Python:", res1)

# 测试2：执行 JavaScript 代码
# 注意：确保 Docker 中存在 ai-sandbox-node:latest 镜像
res2 = execute_tool("execute_code", {"code": "console.log('hello from js');", "language": "javascript"})
print("测试JS:", res2)

# 测试3：故意写错语法
res3 = execute_tool("execute_code", {"code": "prin('hello')", "language": "python"})
print("测试语法错误:", res3)

# 测试4：死循环（移除 timeout 参数，因为工具定义中不支持）
# 警告：这可能会导致测试长时间挂起，直到沙箱内部超时机制触发
print("开始测试超时（可能需要等待沙箱内部超时）...")
res4 = execute_tool("execute_code", {"code": "while True: pass", "language": "python"})
print("测试超时:", res4)