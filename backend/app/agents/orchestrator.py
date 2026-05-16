"""
任务编排器模块
负责将复杂目标分解为子任务，并协调ReAct Agent逐步执行
支持任务状态持久化，可断点续传
"""
from typing import List, Optional, Dict, Any
from .task_store import Task, TaskStatus, task_store
from .planner import Planner
from .executor import Executor


class Orchestrator:
    """
    任务编排器
    负责：
    1. 接收用户目标，调用Planner生成子任务计划
    2. 创建或恢复Task对象，管理任务生命周期
    3. 依次调用Executor执行每个子任务
    4. 汇总所有子任务结果，生成最终答案
    """

    def __init__(self, model: str = "deepseek-chat"):
        """
        初始化编排器
        Args:
            model: 使用的LLM模型名称，默认使用deepseek-chat
        """
        self.model = model
        self.planner = Planner(model)
        self.executor = Executor(model)

    def plan_only(self, goal: str) -> List[str]:
        """
        仅生成执行计划，不实际执行
        Args:
            goal: 用户目标
        Returns:
            子任务列表
        """
        return self.planner.plan(goal)

    def run(self, goal: str, task_id: Optional[str] = None) -> Dict[str, Any]:
        """
        执行任务的主方法
        Args:
            goal: 用户目标描述
            task_id: 可选，如果提供则尝试恢复已有任务
        Returns:
            包含任务状态和结果的字典
        """
        task = None

        # --- 1. 尝试恢复任务或创建新任务 ---
        if task_id and task_id in task_store:
            task = task_store[task_id]
            
            # 如果任务已完成，直接返回结果
            if task.status == TaskStatus.COMPLETED:
                print(f"[Orchestrator] 任务 {task_id} 已完成，直接返回结果")
                return {
                    "task_id": task.task_id,
                    "status": "completed",
                    "goal": task.goal,
                    "final_answer": task.final_answer,
                    "subtasks": task.subtasks,
                    "results": task.results
                }
            
            # 如果任务失败，询问是否重新执行
            if task.status == TaskStatus.FAILED:
                print(f"[Orchestrator] 任务 {task_id} 之前失败，从断点重新执行")
                # 从当前中断的子任务继续
            else:
                print(f"[Orchestrator] 恢复任务: {task.task_id}, 进度: {task.current_subtask}/{len(task.subtasks)}")
        else:
            # 创建新任务
            print(f"[Orchestrator] 创建新任务，目标: {goal}")
            plan = self.planner.plan(goal)
            task = Task(goal=goal, subtasks=plan)
            task_store[task.task_id] = task
            print(f"[Orchestrator] 计划生成完毕，共 {len(plan)} 个子任务")

        # --- 2. 更新任务状态为执行中 ---
        task.status = TaskStatus.RUNNING

        try:
            # --- 3. 逐步执行子任务 ---
            while task.current_subtask < len(task.subtasks):
                idx = task.current_subtask
                subtask = task.subtasks[idx]
                
                print(f"[Orchestrator] ========== 执行子任务 {idx+1}/{len(task.subtasks)} ==========")
                print(f"[Orchestrator] 子任务内容: {subtask}")
                
                # 使用Executor（内部调用ReAct Agent）执行单个子任务
                result = self.executor.execute_task(subtask)
                
                # 保存结果
                task.results.append(result)
                task.current_subtask += 1
                
                print(f"[Orchestrator] 子任务 {idx+1} 完成")
                print(f"[Orchestrator] 当前进度: {task.current_subtask}/{len(task.subtasks)}")

            # --- 4. 所有子任务完成，生成最终汇总 ---
            print(f"[Orchestrator] ========== 所有子任务完成，开始汇总 ==========")
            
            # 构造汇总提示词
            summary_prompt = self._build_summary_prompt(task)
            
            # 调用LLM生成最终答案
            from app.core.llm_client import get_llm_response
            final_answer = get_llm_response(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是一个善于总结的助手，请将以下子任务的执行结果整合成一个完整、清晰的回答。"},
                    {"role": "user", "content": summary_prompt}
                ]
            )
            
            # 保存最终答案
            task.final_answer = final_answer
            task.status = TaskStatus.COMPLETED
            
            print(f"[Orchestrator] 任务 {task.task_id} 完成！")
            
            return {
                "task_id": task.task_id,
                "status": "completed",
                "goal": task.goal,
                "final_answer": final_answer,
                "subtasks": task.subtasks,
                "results": task.results
            }

        except Exception as e:
            # --- 5. 异常处理 ---
            error_msg = str(e)
            print(f"[Orchestrator] 任务执行出错: {error_msg}")
            
            task.status = TaskStatus.FAILED
            task.error = error_msg
            
            return {
                "task_id": task.task_id,
                "status": "failed",
                "goal": task.goal,
                "error": error_msg,
                "current_subtask": task.current_subtask,
                "total_subtasks": len(task.subtasks)
            }

    def _build_summary_prompt(self, task: Task) -> str:
        """构造汇总提示词"""
        parts = [f"用户原始目标：{task.goal}\n"]
        parts.append("以下是各子任务及其执行结果：\n")
        
        for i, (subtask, result) in enumerate(zip(task.subtasks, task.results)):
            parts.append(f"--- 子任务 {i+1} ---")
            parts.append(f"任务内容：{subtask}")
            parts.append(f"执行结果：{result}")
            parts.append("")
        
        parts.append("请将以上所有子任务的结果整合成一个完整、连贯的回答，直接呈现给用户。")
        
        return "\n".join(parts)

    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        查询任务状态
        Args:
            task_id: 任务ID
        Returns:
            任务状态信息字典，如果任务不存在则返回None
        """
        task = task_store.get(task_id)
        if not task:
            return None
        
        return {
            "task_id": task.task_id,
            "goal": task.goal,
            "status": task.status,
            "current_subtask": task.current_subtask,
            "total_subtasks": len(task.subtasks),
            "final_answer": task.final_answer,
            "error": task.error,
            "created_at": task.created_at
        }