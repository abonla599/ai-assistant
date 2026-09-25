"""
TaskAgent - 单智能体，可在循环中调用工具完成任务
完全独立，不依赖 ReActAgent
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # backend 目录

import json
from app.core.llm_client import get_llm_response
from app.tools.executor import execute_tool
from app.tools.registry import is_available

class TaskAgent:
    """
    基础任务智能体：
    - 接收任务描述
    - 循环调用 LLM + 工具，直到得到最终答案
    """
    def __init__(self, name: str, model: str, tools_schema: list, tools: dict,
                 system_prompt: str = "", user_id: str = None):
        # user_id 一路从凭据里带下来：工具执行必须知道"是谁在调"，否则 needs_user
        # 那类工具（查某个人自己的数据）只能一律拒绝。缺身份是 fail-closed，
        # 不是"当成没身份的人"。
        self.name = name
        self.model = model
        self.tools_schema = tools_schema
        self.tools = tools
        self.system_prompt = system_prompt
        self.user_id = user_id
        self.max_turns = 8
        self.verbose = True

    def run(self, task: str) -> str:
        return self._run_fallback(task)

    def _run_fallback(self, task: str) -> str:
        """降级方案：用纯文本提示，让模型决定是否调用工具（基于规则）"""
        # 清单按可用性过滤，和给原生 tool_calls 那条路同一个口径
        # （get_available_tools_schema）。旧写法直接遍历全量注册表：依赖不可用时
        # schema 里没它、提示词里却写着它，模型照样调、照样拿错误，再凭错误硬编答案。
        tool_descriptions = "\n".join([
            f"- {name}: {info['description']}"
            for name, info in self.tools.items()
            if is_available(info)
        ])

        prompt = f"""你是一个智能助手，可以使用以下工具完成任务。

可用工具：
{tool_descriptions}

当你需要使用工具时，请在回复中用以下格式：
<tool_call>
{{"name": "工具名", "arguments": {{"参数名": "值"}}}}
</tool_call>

你会收到工具执行结果，然后继续回答。如果不需要工具，直接给出最终答案。

用户任务: {task}"""

        messages = [
            {"role": "system", "content": "你是一个有能力使用工具的助手。"},
            {"role": "user", "content": prompt}
        ]

        for turn in range(self.max_turns):
            response = get_llm_response(self.model, messages)
            
            # 检查是否有工具调用
            if "<tool_call>" in response:
                # 提取工具调用
                start = response.index("<tool_call>") + len("<tool_call>")
                end = response.index("</tool_call>")
                tool_json = response[start:end].strip()
                try:
                    tool_call = json.loads(tool_json)
                    tool_name = tool_call["name"]
                    tool_args = tool_call.get("arguments", {})
                    
                    if self.verbose:
                        print(f"    [{self.name}] 调用工具: {tool_name}({tool_args})")

                    # 执行工具走执行器，不直接 call 注册表里的函数对象。差三件事：
                    # ① needs_user 的工具必须由服务端补身份，模型自己填的 user_id 作废；
                    # ② 输出要按上下文预算截断；③ 未知工具/参数错误要回一句模型读得懂的话。
                    # 绕过它，这三条同时失效。
                    result = execute_tool(tool_name, tool_args, user_id=self.user_id)

                    # 将结果反馈给模型
                    messages.append({"role": "assistant", "content": response})
                    messages.append({"role": "user", "content": f"工具执行结果: {result}"})
                    
                    if self.verbose:
                        print(f"    [{self.name}] 结果: {result[:100]}...")
                except Exception as e:
                    messages.append({"role": "assistant", "content": response})
                    messages.append({"role": "user", "content": f"工具调用解析错误: {e}，请重试或直接回答。"})
            else:
                # 没有工具调用，视为最终答案
                return response.strip()

        return "达到最大循环次数，任务未完成。"