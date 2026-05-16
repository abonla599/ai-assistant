"""
任务状态存储模块
提供智能体任务的持久化状态管理，支持断点续传和进度查询
当前使用内存字典存储，后续可升级为Redis或数据库
"""
from enum import Enum
from typing import List, Optional
import uuid
from datetime import datetime


class TaskStatus(str, Enum):
    """任务状态枚举"""
    PENDING = "pending"        # 待执行
    RUNNING = "running"        # 执行中
    COMPLETED = "completed"    # 已完成
    FAILED = "failed"          # 执行失败


class Task:
    """任务数据结构"""
    def __init__(self, goal: str, subtasks: List[str] = None):
        self.task_id = str(uuid.uuid4())
        self.goal = goal                          # 用户最初的目标
        self.subtasks = subtasks or []            # 计划子任务列表
        self.current_subtask = 0                  # 当前执行到第几个子任务
        self.results = []                         # 每个子任务的执行结果
        self.status = TaskStatus.PENDING          # 当前状态
        self.created_at = datetime.now().isoformat()  # 创建时间
        self.final_answer = None                  # 最终汇总答案
        self.error = None                         # 错误信息（如果失败）


# 全局任务存储（内存字典，重启后丢失）
# key: task_id, value: Task对象
task_store = {}


def get_task(task_id: str) -> Optional[Task]:
    """根据task_id获取任务对象"""
    return task_store.get(task_id)


def save_task(task: Task) -> None:
    """保存或更新任务"""
    task_store[task.task_id] = task


def delete_task(task_id: str) -> bool:
    """删除任务"""
    if task_id in task_store:
        del task_store[task_id]
        return True
    return False


def get_all_tasks() -> List[Task]:
    """获取所有任务列表"""
    return list(task_store.values())