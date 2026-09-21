"""执行者智能体 - 用 TaskAgent 执行单个子任务"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # backend 目录

from app.agents.task_agent import TaskAgent
from app.tools.registry import tools_registry
from app.tools.registry import get_available_tools_schema


class Executor:
    """工具就取全局注册表那一份。

    这里原先挂的是 `agents/temp_tools.py`——一套平行的临时工具：calculator 是裸
    `eval`（同一件事在 builtin_tools 里已经因为 eval 修过一遍），web_search 更糟：
    它是一张写死的"马斯克/火箭回收"问答表，schema 却对模型宣称"搜索互联网获取信息"。
    也就是说模型以为自己在查资料，实际拿到的是几条编好的句子，然后一本正经地引用进
    回答里——这正是本项目最贵的那类失败：效果没了，还一声不响。
    """

    def __init__(self, model="deepseek-chat"):
        self.model = model

    def execute_task(self, task: str, user_id: str = None) -> str:
        print(f"  [Executor] 开始执行: {task}")
        agent = TaskAgent(
            name="Executor",
            model=self.model,
            tools_schema=get_available_tools_schema(),
            tools=tools_registry,
            user_id=user_id,
            system_prompt="你是一个执行助手。用可用工具完成任务，返回简洁准确的结果。"
        )
        result = agent.run(task)
        print(f"  [Executor] 完成: {result[:80]}...")
        return result
