"""执行者智能体 - 用 TaskAgent 执行单个子任务"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # backend 目录

from app.agents.task_agent import TaskAgent
from app.agents.temp_tools import get_temp_tools_schema, TEMP_TOOLS

class Executor:
    def __init__(self, model="deepseek-chat"):
        self.model = model
        self.tools_schema = get_temp_tools_schema()
        self.tools = TEMP_TOOLS

    def execute_task(self, task: str) -> str:
        print(f"  [Executor] 开始执行: {task}")
        agent = TaskAgent(
            name="Executor",
            model=self.model,
            tools_schema=self.tools_schema,
            tools=self.tools,
            system_prompt="你是一个执行助手。用可用工具完成任务，返回简洁准确的结果。"
        )
        result = agent.run(task)
        print(f"  [Executor] 完成: {result[:80]}...")
        return result