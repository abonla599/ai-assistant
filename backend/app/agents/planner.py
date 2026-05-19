"""规划者智能体 - 分解复杂目标为子任务"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # backend 目录

from app.core.llm_client import get_llm_response
import json

class Planner:
    def __init__(self, model="deepseek-chat"):
        self.model = model

    def plan(self, goal: str) -> list:
        prompt = f"""你是一个任务规划专家。请将以下复杂目标分解为 3-5 个清晰的子任务步骤，每步一句话。

目标: {goal}

严格用 JSON 数组格式回复，如: ["步骤1描述", "步骤2描述"]
不要添加任何额外文字，只输出 JSON 数组。"""

        response = get_llm_response(self.model, [
            {"role": "system", "content": "你只输出 JSON 数组，无任何其他内容。"},
            {"role": "user", "content": prompt}
        ])

        # 解析 JSON
        try:
            clean = response.strip()
            if clean.startswith("```"):
                lines = clean.split("\n")
                clean = "\n".join(lines[1:-1])
            plan = json.loads(clean)
            if isinstance(plan, list) and len(plan) > 0:
                return plan
        except:
            pass

        # 降级：按行拆分
        lines = []
        for line in response.splitlines():
            stripped = line.strip("-* 1234567890.、 ")
            if stripped and len(stripped) > 2:
                lines.append(stripped)
        return lines[:5]