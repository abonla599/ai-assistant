"""
测试多智能体协作
运行: python backend/test_orchestrator.py
"""
import sys
from pathlib import Path

# 将 backend 目录添加到 sys.path，确保能找到 app 包
sys.path.insert(0, str(Path(__file__).parent))

from app.agents.orchestrator import Orchestrator

# 创建协调者
orch = Orchestrator(model="deepseek-chat")

# 测试目标：一个需要多步完成的任务
goal = "查询马斯克有多少家公司，分别是什么，并计算 15 * 28，最后把结果记录下来"

print("\n" + "="*60)
print("测试多智能体协作框架")
print("="*60)

result = orch.run(goal)

print("\n" + "="*60)
print("测试完成！")
print("="*60)