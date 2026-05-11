from app.sandbox.sandbox_manager import SandboxManager

sm = SandboxManager()

# 测试1：Python
print("=" * 30)
print("测试1：Python 正常执行")
result = sm.run_code("print('Hello Python!')", "python")
print("stdout:", result.get("stdout", ""))
print("stderr:", result.get("stderr", ""))
print("error:", result.get("error"))
print("耗时:", result.get("execution_time", "无"))

# 测试2：JavaScript
print("=" * 30)
print("测试2：JavaScript 正常执行")
result = sm.run_code("console.log('Hello JavaScript!');", "javascript")
print("stdout:", result.get("stdout", ""))
print("stderr:", result.get("stderr", ""))
print("error:", result.get("error"))
print("耗时:", result.get("execution_time", "无"))

# 测试3：JS 超时
print("=" * 30)
print("测试3：JavaScript 死循环（等待3秒超时）")
result = sm.run_code("while(true){}", "javascript", timeout=3)
print("stdout:", result.get("stdout", ""))
print("stderr:", result.get("stderr", ""))
print("error:", result.get("error"))
print("耗时:", result.get("execution_time", "无"))

# 测试4：在受保护的文件系统写入文件（应该失败）
print("=" * 30)
print("测试4：尝试写入根目录（应返回只读文件系统错误）")
code4 = """
import os
try:
    with open('/test.txt', 'w') as f:
        f.write('hello')
    print('写入成功')
except Exception as e:
    print(f'写入失败: {e}')
"""
result = sm.run_code(code4, "python", timeout=3)
print("stdout:", result.get("stdout", ""))
print("stderr:", result.get("stderr", ""))