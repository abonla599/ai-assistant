"""
ReAct (Reasoning + Acting) 智能体模块
实现思考-行动-观察循环，支持工具调用和多轮推理
"""
import time
import json
from typing import List, Dict, Any, Optional
from app.core.llm_client import get_llm_response
from app.tools.executor import execute_tool
from app.tools.registry import get_all_tools_schema


class ReActAgent:
    """
    ReAct智能体
    核心流程：
    1. Thought（思考）：分析当前状态，决定下一步行动
    2. Action（行动）：调用工具或给出最终答案
    3. Observation（观察）：获取工具执行结果
    4. 重复以上步骤直到得出最终答案或达到最大轮次
    """

    def __init__(
        self,
        model: str = "deepseek-chat",
        max_turns: int = 10,
        verbose: bool = True
    ):
        """
        初始化ReAct智能体
        Args:
            model: 使用的LLM模型
            max_turns: 最大思考-行动轮次
            verbose: 是否打印详细日志
        """
        self.model = model
        self.max_turns = max_turns
        self.verbose = verbose
        self.tools_schema = get_all_tools_schema()

    def run(
        self,
        task: str,
        context: Optional[List[Dict[str, str]]] = None,
        max_duration: int = 120
    ) -> str:
        """
        执行任务的主方法
        Args:
            task: 任务描述
            context: 可选的上下文消息列表
            max_duration: 最大执行时间（秒），默认120秒
        Returns:
            最终答案字符串
        """
        # --- 超时控制 ---
        start_time = time.time()
        
        # --- 初始化消息列表 ---
        messages = []
        
        # 系统提示词
        system_prompt = self._build_system_prompt()
        messages.append({"role": "system", "content": system_prompt})
        
        # 添加上下文（如果有）
        if context:
            messages.extend(context)
        
        # 添加用户任务
        messages.append({"role": "user", "content": task})
        
        if self.verbose:
            print(f"[ReActAgent] 开始执行任务: {task}")
            print(f"[ReActAgent] 最大轮次: {self.max_turns}, 最大时长: {max_duration}秒")
        
        # --- ReAct 主循环 ---
        for turn in range(self.max_turns):
            # ========== 超时检查 ==========
            elapsed_time = time.time() - start_time
            if elapsed_time > max_duration:
                timeout_msg = f"任务执行超时（已执行{elapsed_time:.1f}秒，超过{max_duration}秒限制）。以下是目前已获得的部分结果。"
                if self.verbose:
                    print(f"[ReActAgent] ⏰ {timeout_msg}")
                
                # 尝试强制总结当前已有的信息
                try:
                    summary = self._force_summarize(messages, task)
                    return f"{timeout_msg}\n\n{summary}"
                except:
                    return timeout_msg
            
            if self.verbose:
                remaining_time = max_duration - elapsed_time
                print(f"[ReActAgent] ====== 第 {turn+1}/{self.max_turns} 轮（剩余{remaining_time:.0f}秒）======")
            
            # --- 1. 调用LLM获取响应 ---
            try:
                response = get_llm_response(
                    model=self.model,
                    messages=messages,
                    tools=self.tools_schema,
                    temperature=0.7
                )
            except Exception as e:
                error_msg = f"调用LLM出错: {str(e)}"
                if self.verbose:
                    print(f"[ReActAgent] ❌ {error_msg}")
                return error_msg
            
            # --- 2. 解析LLM响应 ---
            if self.verbose:
                print(f"[ReActAgent] LLM响应: {response[:200]}...")
            
            # 检查是否有工具调用
            tool_calls = self._extract_tool_calls(response)
            
            if tool_calls:
                # --- 3. 执行工具调用（Action） ---
                for tool_call in tool_calls:
                    tool_name = tool_call["name"]
                    tool_args = tool_call.get("arguments", {})
                    
                    if self.verbose:
                        print(f"[ReActAgent] 🔧 调用工具: {tool_name}")
                        print(f"[ReActAgent] 参数: {json.dumps(tool_args, ensure_ascii=False)}")
                    
                    # 执行工具
                    try:
                        tool_result = execute_tool(tool_name, tool_args)
                        if self.verbose:
                            print(f"[ReActAgent] ✅ 工具结果: {str(tool_result)[:200]}...")
                    except Exception as e:
                        tool_result = f"工具执行错误: {str(e)}"
                        if self.verbose:
                            print(f"[ReActAgent] ❌ {tool_result}")
                    
                    # --- 4. 将工具结果添加到消息中（Observation） ---
                    # 添加助手的工具调用消息
                    messages.append({
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": f"call_{turn}",
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": json.dumps(tool_args, ensure_ascii=False)
                            }
                        }]
                    })
                    
                    # 添加工具返回结果
                    messages.append({
                        "role": "tool",
                        "tool_call_id": f"call_{turn}",
                        "content": str(tool_result)
                    })
            else:
                # --- 5. 没有工具调用，视为最终答案 ---
                if self.verbose:
                    print(f"[ReActAgent] 🎯 获得最终答案")
                return response
        
        # --- 6. 达到最大轮次，强制总结 ---
        if self.verbose:
            print(f"[ReActAgent] ⚠️ 达到最大轮次 {self.max_turns}，强制总结")
        
        try:
            return self._force_summarize(messages, task)
        except:
            return "抱歉，任务执行达到最大轮次限制，未能得出完整结论。"

    def _build_system_prompt(self) -> str:
        """构建系统提示词"""
        # 格式化工具列表
        tool_descriptions = []
        for tool in self.tools_schema:
            tool_descriptions.append(f"- {tool['function']['name']}: {tool['function']['description']}")
        
        tools_text = "\n".join(tool_descriptions) if tool_descriptions else "无可用工具"
        
        return f"""你是一个智能助手，能够使用工具来完成复杂任务。

可用工具：
{tools_text}

工作流程：
1. 分析用户的任务，思考需要使用哪些工具
2. 调用相应的工具获取信息或执行操作
3. 根据工具返回的结果，决定下一步行动
4. 当你认为已经收集到足够的信息时，直接给出最终答案

注意事项：
- 每次只调用必要的工具，不要过度调用
- 如果工具返回错误，尝试其他方法或向用户说明
- 最终答案应该清晰、完整，直接回答用户的问题
- 如果任务无法完成，诚实告知用户原因
"""

    def _extract_tool_calls(self, response: str) -> List[Dict[str, Any]]:
        """
        从LLM响应中提取工具调用
        支持两种格式：
        1. 原生tool_calls格式（OpenAI/DeepSeek API格式）
        2. JSON标记格式（备选）
        """
        # 尝试解析为包含tool_calls的响应
        # 这里简化处理，实际使用时需要根据LLM API的具体响应格式调整
        try:
            # 如果response是字符串，尝试查找JSON格式的工具调用
            if isinstance(response, str):
                # 查找 {"tool": "xxx", "args": {...}} 格式
                import re
                pattern = r'```json\s*\n(.*?)\n```'
                matches = re.findall(pattern, response, re.DOTALL)
                tool_calls = []
                for match in matches:
                    try:
                        data = json.loads(match)
                        if "tool" in data:
                            tool_calls.append({
                                "name": data["tool"],
                                "arguments": data.get("args", {})
                            })
                    except:
                        pass
                return tool_calls
            return []
        except Exception:
            return []

    def _force_summarize(self, messages: List[Dict], task: str) -> str:
        """强制总结当前对话"""
        summary_prompt = f"请基于以上对话历史，对任务「{task}」给出你目前能得出的最佳答案。即使信息不完整，也请尽量提供有价值的内容。"
        
        summarize_messages = messages.copy()
        summarize_messages.append({"role": "user", "content": summary_prompt})
        
        return get_llm_response(
            model=self.model,
            messages=summarize_messages,
            temperature=0.5
        )