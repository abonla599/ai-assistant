from app.sandbox.sandbox_manager import SandboxManager

sm = SandboxManager()

# 测试 1：正常代码
code1 = """
print("Hello from sandbox")
for i in range(3):
    print(f"Number: {i}")
"""
res = sm.run_code(code1)
print("=== 正常代码输出 ===")
print("stdout:", res["stdout"])
print("stderr:", res["stderr"])
print("error:", res.get("error"))
print()

# 测试 2：死循环（会被 timeout 杀死）
code2 = "while True: pass"
res = sm.run_code(code2, timeout=3)
print("=== 死循环测试 ===")
print("stdout:", res["stdout"])
print("stderr:", res["stderr"])
print("error:", res.get("error"))
print()

# 测试 3：尝试联网或危险操作（应受到限制）
code3 = """
import socket
try:
    s = socket.socket()
    s.connect(("google.com", 80))
    print("连接成功（不应该出现）")
except Exception as e:
    print("网络不可用：", e)
"""
res = sm.run_code(code3)
print("=== 网络限制测试 ===")
print("stdout:", res["stdout"])
print("error:", res.get("error"))