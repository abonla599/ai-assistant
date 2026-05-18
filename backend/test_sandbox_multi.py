from app.sandbox.sandbox_manager import SandboxManager

sm = SandboxManager()

# 测试 1：Python 正常执行
print("=== Python 正常代码 ===")
result = sm.run_code("print('hello python')", "python")
print(f"stdout: {result['stdout']}")
print(f"error: {result.get('error')}")
print(f"执行时间: {result.get('execution_time', 'N/A')}s")
print()

# 测试 2：JavaScript 正常执行
print("=== JavaScript 正常代码 ===")
result = sm.run_code("console.log('hello js');", "javascript")
print(f"stdout: {result['stdout']}")
print(f"error: {result.get('error')}")
print(f"执行时间: {result.get('execution_time', 'N/A')}s")
print()

# 测试 3：JavaScript 死循环（超时）
print("=== JavaScript 死循环测试 ===")
result = sm.run_code("while(true);", "javascript", timeout=3)
print(f"stdout: {result['stdout']}")
print(f"error: {result.get('error')}")
print()

# 测试 4：JavaScript 网络隔离测试
print("=== JavaScript 网络限制测试 ===")
code4 = """
try {
    const http = require('http');
    http.get('http://google.com', (res) => {
        console.log('连接成功（不应该出现）');
    });
} catch (e) {
    console.log('网络不可用：' + e.message);
}
"""
result = sm.run_code(code4, "javascript", timeout=5)
print(f"stdout: {result['stdout']}")
print(f"error: {result.get('error')}")