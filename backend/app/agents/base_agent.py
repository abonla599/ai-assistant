from abc import ABC, abstractmethod

class BaseAgent(ABC):
    def __init__(self, name: str, tools: dict = None):
        self.name = name
        self.tools = tools or {}  # 工具字典：名称 -> (函数, schema)

    @abstractmethod
    def run(self, task: str, context: list = None) -> str:
        """运行智能体，返回最终结果"""
        pass