"""沙箱并发：多个任务同时起容器时不许互相打翻。

单条任务的"能不能跑、缺什么"由 conftest 的 sandbox_language 统一决定，
这里只负责"并发"这件事本身。
"""
import concurrent.futures

import pytest

from app.sandbox.sandbox_manager import SandboxManager


@pytest.fixture
def task_id(request):
    """提供测试任务 ID"""
    return getattr(request, 'param', 0)


def _code_for(task_id):
    return f"print('Task {task_id}: 1+1=', 1+1)"


def test_run(task_id, sandbox_language):
    sandbox_language("python")
    result = SandboxManager().run_code(_code_for(task_id), "python")
    assert result.get("error") is None, f"任务 {task_id} 执行失败: {result.get('error')}"
    # 只断 error is None 是假绿：容器"连代码文件都读不到"时 error 也是 None，
    # 失败文本躺在 stdout 里。所以必须断那段 print 真的发生了。
    assert result.get("exit_code") == 0, f"容器非零退出: {result}"
    assert "1+1= 2" in result["stdout"], f"代码没有真的执行，stdout={result['stdout']!r}"


def main():
    """手工压测：并发跑一轮，缺 Docker 就直接算失败（这里没人替你 skip）。"""
    print("=== 沙箱并发压力测试 ===")
    total_tasks = 20
    max_workers = 10

    def run(i):
        try:
            return SandboxManager().run_code(_code_for(i), "python")
        except Exception as e:
            return {"error": repr(e)}

    sm = SandboxManager()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(run, i): i for i in range(total_tasks)}
        success = 0
        error = 0
        for future in concurrent.futures.as_completed(futures):
            tid = futures[future]
            result = future.result()
            if result.get("error") is None:
                success += 1
                print(f"✓ 任务 {tid}: 成功 | 耗时: {result.get('execution_time', 0):.2f}s")
            else:
                error += 1
                print(f"✗ 任务 {tid}: 失败 | {result.get('error')}")

    print(f"\n总计: {total_tasks} | 成功: {success} | 失败: {error}")
    if sm.unavailable_reason:
        print(f"（Docker 不可用原因：{sm.unavailable_reason}）")


if __name__ == "__main__":
    main()
