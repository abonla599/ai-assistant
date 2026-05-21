# backend/test_sandbox_basic.py
from app.sandbox.sandbox_manager import SandboxManager

sm = SandboxManager()

# 测试1：最简单的 Python 代码
result = sm.run_code("print('hello from sandbox')", language="python")
print("测试1结果：", result)
# 预期输出类似：{'stdout': 'hello from sandbox\n', 'stderr': '', 'error': None}