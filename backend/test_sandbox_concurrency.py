import concurrent.futures
from app.sandbox.sandbox_manager import SandboxManager
import subprocess

def run_one_task(task_id):
    sm = SandboxManager()
    result = sm.run_code(f"print('Task {task_id} done')")
    return result.get("stdout", ""), result.get("error")

print("开始并发测试：10个线程跑20个任务")
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
    futures = [executor.submit(run_one_task, i) for i in range(20)]
    for f in concurrent.futures.as_completed(futures):
        stdout, error = f.result()
        if error:
            print(f"[失败] {error}")
        else:
            print(f"[成功] {stdout.strip()}")

# 检查是否有残留容器
print("\n检查残留容器：")
result = subprocess.run(["docker", "ps", "-a"], capture_output=True, text=True)
print(result.stdout)