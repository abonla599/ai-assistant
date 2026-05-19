import json
import uuid
from typing import List, Dict, Any

# 导入你自己的模块（路径根据实际情况调整，这里假设都是 app.xxx）
from app.core.llm_client import get_llm_response, MODEL_CONFIGS
from app.memory.memory_manager import MemoryManager
from app.tools.registry import get_all_tools_schema
from app.tools.executor import execute_tool
# from app.agents.react_agent import ReActAgent  # 暂时注释，以后集成

# 共享实例（简单单例，后续优化）
memory_manager = MemoryManager()

class ChatPipeline:
    def __init__(self, user_id: str = "default_user"):
        self.user_id = user_id
        self.memory = memory_manager
        self.tools_schema = get_all_tools_schema()

    def process(self, model: str, messages: List[Dict]) -> Dict[str, Any]:
        """
        主处理流程：
        1. 从 messages 提取用户最新输入
        2. 检索记忆注入上下文
        3. 调用 LLM，可能产生工具调用（简单循环处理工具调用）
        4. 自动保存新记忆
        5. 返回回复和消息ID
        """
        # 1. 提取最新用户输入
        user_input = ""
        for msg in reversed(messages):
            if msg["role"] == "user":
                user_input = msg["content"]
                break
        if not user_input:
            return {"reply": "请提供输入内容", "message_id": None}

        # 2. 注入记忆上下文
        enriched_messages = self._inject_memory(messages, user_input)

        # 3. 调用模型（带工具循环）
        final_reply = self._call_model_with_tool_loop(model, enriched_messages)

        # 4. 自动保存对话摘要到记忆
        self._save_interaction(user_input, final_reply)

        msg_id = str(uuid.uuid4())
        return {"reply": final_reply, "message_id": msg_id}

    def _inject_memory(self, messages: List[Dict], query: str) -> List[Dict]:
        """检索相关记忆并插入到消息最前面"""
        try:
            memories = self.memory.search_memory(self.user_id, query, top_k=3)
            if memories:
                mem_text = "以下是用户相关的历史信息（可能有用）：\n" + \
                           "\n".join([f"- {doc}" for doc, _, _ in memories])
                # 插入为 system 消息（如果已有 system 消息，则追加内容）
                if messages and messages[0]["role"] == "system":
                    messages[0]["content"] += "\n\n" + mem_text
                else:
                    messages.insert(0, {"role": "system", "content": mem_text})
        except Exception as e:
            print(f"记忆检索失败（不影响主流程）: {e}")
        return messages

    def _call_model_with_tool_loop(self, model: str, messages: List[Dict], max_turns=5) -> str:
        """支持工具调用的对话循环，类似ReAct但简化版，直接用OpenAI SDK"""
        from openai import OpenAI
        import os
        from dotenv import load_dotenv
        load_dotenv()

        # 根据 model 选择客户端
        config = MODEL_CONFIGS.get(model)
        if not config:
            return f"不支持的模型: {model}"
        api_key = os.getenv(config["api_key_env"])
        if not api_key:
            return f"缺少API密钥: {config['api_key_env']}"
        client = OpenAI(api_key=api_key, base_url=config["base_url"])

        # 复制消息列表，避免修改原始数据
        msgs = list(messages)

        for turn in range(max_turns):
            response = client.chat.completions.create(
                model=config["model_name"],
                messages=msgs,
                tools=self.tools_schema,  # 传递工具定义
                tool_choice="auto"
            )
            msg = response.choices[0].message

            if msg.tool_calls:
                # 执行工具，并将结果追加回消息
                msgs.append(msg.model_dump())
                for tool_call in msg.tool_calls:
                    name = tool_call.function.name
                    args = json.loads(tool_call.function.arguments)
                    print(f"[Pipeline] 调用工具: {name}({args})")
                    try:
                        result = execute_tool(name, args)
                    except Exception as e:
                        result = f"工具执行错误: {e}"
                    # 将工具结果作为 tool 消息添加
                    msgs.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": str(result)
                    })
            else:
                # 无工具调用，返回文本
                return msg.content or "（模型未返回内容）"
        return "已达到最大循环次数，任务可能未完成。"

    def _save_interaction(self, user_input: str, ai_reply: str):
        """将本轮对话摘要存入记忆"""
        try:
            # 简单摘要：直接使用用户输入的前100字符作为记忆内容（后期可用模型摘要）
            summary = f"用户问: {user_input[:100]}；AI答: {ai_reply[:100]}"
            self.memory.add_memory(self.user_id, summary)
        except Exception as e:
            print(f"记忆保存失败: {e}")