import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import concurrent.futures
from app.sandbox.sandbox_manager import SandboxManager

def test_run(task_id):
    sm = SandboxManager()
    code = f"print('Task {task_id}: 1+1=', 1+1)"
    result = sm.run_code(code, "python")
    return task_id, result

def main():
    print("=== 沙箱并发压力测试 ===")
    total_tasks = 20
    max_workers = 10

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(test_run, i) for i in range(total_tasks)]
        success = 0
        error = 0
        for future in concurrent.futures.as_completed(futures):
            task_id, result = future.result()
            if result.get("error") is None:
                success += 1
                print(f"✓ 任务 {task_id}: 成功 | 耗时: {result.get('execution_time', 'N/A'):.2f}s")
            else:
                error += 1
                print(f"✗ 任务 {task_id}: 失败 | {result.get('error')}")

    print(f"\n总计: {total_tasks} | 成功: {success} | 失败: {error}")
    print(f"最终统计: {SandboxManager.get_stats()}")

if __name__ == "__main__":
    main()
