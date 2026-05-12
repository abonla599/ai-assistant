from app.sandbox.sandbox_manager import SandboxManager

sm = SandboxManager()

# Test 1: Normal code
print("=" * 30)
print("Test 1: Normal code")
code1 = """
print('Hello from sandbox!')
for i in range(3):
    print(f'Loop {i+1}')
"""
result = sm.run_code(code1)
print("stdout:", result["stdout"])
print("stderr:", result["stderr"])
print("error:", result["error"])

# Test 2: Infinite loop (should timeout)
print("=" * 30)
print("Test 2: Infinite loop (3s timeout)")
code2 = "while True: pass"
result = sm.run_code(code2, timeout=3)
print("stdout:", result["stdout"])
print("stderr:", result["stderr"])
print("error:", result["error"])

# Test 3: Division by zero
print("=" * 30)
print("Test 3: Division by zero")
code3 = "print(1/0)"
result = sm.run_code(code3, timeout=3)
print("stdout:", result["stdout"])
print("stderr:", result["stderr"])
print("error:", result["error"])