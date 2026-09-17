import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import concurrent.futures
from app.sandbox.sandbox_manager import SandboxManager


@pytest.fixture
def task_id(request):
    """提供测试任务 ID"""
    return getattr(request, 'param', 0)


# 这一条是真的要起容器，所以它依赖两样机器上不一定有的东西：Docker 守护进程，
# 以及 docker/sandbox/Dockerfile 造出来的 ai-sandbox:latest 镜像。CI 的工作流里
# 显式构建它（见 .github/workflows/tests.yml），所以 CI 上这条是真在跑。
# 缺哪一样就 skip 并把那一样说出来：缺 Docker 不是产品的错，但记成"跑过了"是
# 假的绿，一律 failure 又让每台没装 Docker Desktop 的开发机长红。
IMAGE_BUILD_CMD = "docker build -t ai-sandbox:latest -f docker/sandbox/Dockerfile docker/sandbox"


def _sandbox_unavailable(sm):
    """沙箱起不来时返回一句原因，起得来则返回 None。"""
    if sm.client is None:
        return f"Docker 守护进程不可用：{sm.unavailable_reason}"
    try:
        sm.client.images.get(SandboxManager.LANGUAGE_IMAGES["python"])
    except Exception as e:                      # ImageNotFound / APIError / 权限…
        return f"沙箱镜像缺失（{e}），先执行一次：{IMAGE_BUILD_CMD}"
    return None


def _run_in_sandbox(task_id):
    """跑一次沙箱并交出结果字典。"""
    sm = SandboxManager()
    reason = _sandbox_unavailable(sm)
    if reason is not None:
        pytest.skip(f"沙箱用例需要 Docker 与 ai-sandbox:latest：{reason}")
    code = f"print('Task {task_id}: 1+1=', 1+1)"
    return sm.run_code(code, "python")


def test_run(task_id):
    result = _run_in_sandbox(task_id)
    assert result.get("error") is None, f"任务 {task_id} 执行失败: {result.get('error')}"


def main():
    print("=== 沙箱并发压力测试 ===")
    total_tasks = 20
    max_workers = 10

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_run_in_sandbox, i): i for i in range(total_tasks)}
        success = 0
        error = 0
        for future in concurrent.futures.as_completed(futures):
            task_id = futures[future]
            try:
                result = future.result()
            except BaseException as e:
                # pytest.skip 抛的 Skipped 继承自 BaseException，`except Exception`
                # 接不住；压测脚本要把"没跑成"记成失败，而不是整场崩在这里。
                result = {"error": repr(e)}
            if result.get("error") is None:
                success += 1
                print(f"✓ 任务 {task_id}: 成功 | 耗时: {result.get('execution_time', 'N/A'):.2f}s")
            else:
                error += 1
                print(f"✗ 任务 {task_id}: 失败 | {result.get('error')}")

    print(f"\n总计: {total_tasks} | 成功: {success} | 失败: {error}")

if __name__ == "__main__":
    main()
