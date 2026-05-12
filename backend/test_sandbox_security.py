from app.sandbox.sandbox_manager import SandboxManager

sm = SandboxManager()

# 测试1：尝试提权操作（应该显示普通用户ID）
print("=" * 30)
print("测试1：检查容器是否非root用户")
code1 = "import os; print('用户ID:', os.geteuid())"
result = sm.run_code(code1, timeout=5)
print("stdout:", result.get("stdout"))
print("error:", result.get("error"))
print("剩余次数:", result.get("daily_runs_remaining"))
print()

# 测试2：查看每日限制是否工作
print("=" * 30)
print("测试2：连续执行3次，看剩余次数递减")
for i in range(3):
    result = sm.run_code("print(1+1)")
    print(f"第{i+1}次，剩余次数: {result.get('daily_runs_remaining')}, 总次数: {result.get('total_runs')}")