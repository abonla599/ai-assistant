import json
import uuid
from typing import List, Dict, Any

# 导入路径统一用 app. 前缀：裸模块名（memory.*、tools.*）只在以脚本方式
# 启动 backend/app/main.py 时恰好可用，打包后会 ImportError，并被上层的
# try/except 静默吞掉，导致记忆与工具能力在 EXE 里悄悄失效。
from app.core.providers import store, build_client
# 复用 memory_router 已选定的后端单例：本模块此前自行 new 了第二个 MemoryManager，
# 导致同进程两个 ChromaDB 客户端开同一个库，且测试时会绕过假存储写进真实记忆库。
from app.memory.memory_router import memory_manager
from app.preference_analyzer import read_preference
from app.tools.registry import get_all_tools_schema
from app.tools.executor import execute_tool
# from app.agents.react_agent import ReActAgent  # 暂时注释，以后集成
from app.tools.builtin_tools import *


class ChatPipeline:
    def __init__(self, user_id: str = "default_user"):
        self.user_id = user_id
        # 测试/CI 下为 None（走内存假存储），此时跳过记忆注入与自动保存
        self.memory = memory_manager
        self.tools_schema = get_all_tools_schema()

    @staticmethod
    def text_of(content) -> str:
        """取消息里的纯文本。带图片附件时 content 是多模态数组。"""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                part.get("text", "") for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        return "" if content is None else str(content)

    def process(self, model: str, messages: List[Dict], provider_id: str = None) -> Dict[str, Any]:
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
            if msg.get("role") == "user":
                user_input = self.text_of(msg.get("content"))
                break
        if not user_input:
            return {"reply": "请提供输入内容", "message_id": None}

        # 2. 注入记忆与用户偏好上下文
        enriched_messages, used_memory_ids = self.inject_context(messages, user_input)

        # 3. 调用模型（带工具循环）
        final_reply = self._call_model_with_tool_loop(model, enriched_messages,
                                                      provider_id=provider_id)

        # 4. 自动保存对话摘要到记忆
        self.save_interaction(user_input, final_reply)

        msg_id = str(uuid.uuid4())
        return {"reply": final_reply, "message_id": msg_id,
                "used_memory_ids": used_memory_ids}

    def inject_context(self, messages: List[Dict], query: str):
        """注入记忆与用户偏好摘要（非流式与流式共用）。

        返回 (消息列表, 本次用到的记忆 id 列表)——后者用于反馈闭环，
        否则无法知道该给哪些记忆加权。
        """
        used_ids = []
        try:
            if self.memory is not None:
                memories = self.memory.search_memory(self.user_id, query, top_k=3)
                if memories:
                    lines = []
                    for doc, _, meta in memories:
                        mid = (meta or {}).get("memory_id")
                        if mid:
                            used_ids.append(mid)
                        lines.append(f"- {doc}")
                    self._append_system(
                        messages, "以下是用户相关的历史信息（可能有用）：\n" + "\n".join(lines))
        except Exception as e:
            print(f"记忆检索失败（不影响主流程）: {e}")

        try:
            preference = read_preference()
            if preference:
                self._append_system(
                    messages, "根据用户历史反馈得到的偏好，请遵循：\n" + preference)
        except Exception as e:
            print(f"偏好读取失败（不影响主流程）: {e}")

        return messages, used_ids

    @staticmethod
    def _append_system(messages: List[Dict], text: str) -> None:
        """追加到已有 system 消息（前端可能已带角色设定），没有则新建。"""
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] += "\n\n" + text
        else:
            messages.insert(0, {"role": "system", "content": text})

    def _call_model_with_tool_loop(self, model: str, messages: List[Dict], max_turns=5,
                                   provider_id: str = None) -> str:
        """支持工具调用的对话循环，类似 ReAct 的简化版。

        配置缺失或调用失败一律抛异常：把故障当回复文本返回，会让错误写进会话
        历史，并被上层当作模型输出继续加工。
        """
        provider = store.resolve(provider_id, legacy_model=model)
        client = build_client(provider)

        # 复制消息列表，避免修改原始数据
        msgs = list(messages)

        for turn in range(max_turns):
            response = client.chat.completions.create(
                model=provider["model"],
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

    def save_interaction(self, user_input: str, ai_reply: str):
        """将本轮对话摘要存入记忆"""
        try:
            if self.memory is None:
                return
            # 简单摘要：直接使用用户输入的前100字符作为记忆内容（后期可用模型摘要）
            summary = f"用户问: {user_input[:100]}；AI答: {ai_reply[:100]}"
            self.memory.add_memory(self.user_id, summary)
        except Exception as e:
            print(f"记忆保存失败: {e}")