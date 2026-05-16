"""协调者 - 规划 + 逐个执行 + 汇总"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # backend 目录

from app.agents.planner import Planner
from app.agents.executor import Executor
from app.core.llm_client import get_llm_response

class Orchestrator:
    def __init__(self, model="deepseek-chat"):
        self.model = model
        self.planner = Planner(model)
        self.executor = Executor(model)

    def run(self, goal: str) -> str:
        print(f"\n{'='*50}")
        print(f"[Orchestrator] 目标: {goal}")
        print(f"{'='*50}")

        # 1. 规划
        plan = self.planner.plan(goal)
        print(f"\n[Orchestrator] 计划分解为 {len(plan)} 步:")
        for i, step in enumerate(plan, 1):
            print(f"  {i}. {step}")

        # 2. 逐步执行
        results = []
        for i, subtask in enumerate(plan, 1):
            print(f"\n[Orchestrator] --- 执行子任务 {i}/{len(plan)} ---")
            result = self.executor.execute_task(subtask)
            results.append({
                "step": i,
                "task": subtask,
                "result": result
            })

        # 3. 汇总
        print(f"\n[Orchestrator] 汇总结果...")
        summary_prompt = "请根据以下子任务执行结果，整合成一个完整的最终回答：\n\n"
        for r in results:
            summary_prompt += f"子任务{r['step']}: {r['task']}\n结果: {r['result']}\n\n"
        summary_prompt += "请给出最终完整回答："

        final_answer = get_llm_response(self.model, [
            {"role": "user", "content": summary_prompt}
        ])

        print(f"\n[Orchestrator] 最终答案:\n{final_answer}")
        return final_answer