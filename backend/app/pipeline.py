import json
import uuid
from typing import List, Dict, Any

# 导入路径统一用 app. 前缀：裸模块名（memory.*、tools.*）只在以脚本方式
# 启动 backend/app/main.py 时恰好可用，打包后会 ImportError，并被上层的
# try/except 静默吞掉，导致记忆与工具能力在 EXE 里悄悄失效。
from app.core.providers import store, build_client
from app.core import usage   # 账本：与流式那一条同一个口径，别各记一套
from app.core import schedule   # "今天"的口径：见 _today_line
from app.core.buildinfo import build_version   # 本部署是哪个版本：见 _env_line
# 复用 memory_router 已选定的后端单例：本模块此前自行 new 了第二个 MemoryManager，
# 导致同进程两个 ChromaDB 客户端开同一个库，且测试时会绕过假存储写进真实记忆库。
from app.memory.memory_router import memory_manager
from app.memory import signals
from app.preference_analyzer import read_preference
from app.tools.registry import get_available_tools_schema
from app.tools.executor import execute_tool
# from app.agents.react_agent import ReActAgent  # 暂时注释，以后集成
from app.tools.builtin_tools import *


def _today_line() -> str:
    """给模型的那句「今天是 2026-09-22 周二。」

    为什么要有：模型此前从来收不到服务端日期，于是它既不知道"今天"是哪天，也无从
    把"明年""下周三"换算成具体日子、更不知道该不该为此去搜——2026-09-22 那次
    「2027 年研究生简章搜不到、今天几号却答得出来」就是这个洞。`schedule.py`
    的 `render_for_model` 里那句"它从提示词里拿不到今天几号"说的正是本函数之前的事。

    口径：走 `schedule.today()`，不在这里另起一份 `datetime.now()`——那是这个仓库
    里"今天"的既有权威（提醒功能与它同一份，都以服务端本地日期为准），两份定义迟早
    会在跨零点时给出两个不同的"今天"。星期名同样复用 schedule 那一张表：两处各写一份
    周一到周日，改一处就会给模型两个不同的说法。
    """
    day = schedule.today()
    return f"今天是 {day} {schedule.weekday_of(day)}。"


def _env_line() -> str:
    """告诉模型它运行在什么环境里、这套部署是哪个版本。

    为什么要有：与 _today_line 同一类洞——模型此前收不到任何关于"自己是谁、住在
    哪"的事实，被问到「这个软件版本号多少」「你知道自己在哪个应用里吗」只能凭
    训练语料猜或答不知道。版本号唯一来源是构建戳 version.txt（app.core.buildinfo），
    这里绝不自造第二个事实来源；取不到就如实说"未登记"，宁缺毋假。
    """
    ver = build_version()
    shown = ver if ver else "未登记（构建时未带版本戳）"
    return (f"你运行在「AI 助手」应用内，对话由它的后端服务转发；当前服务端版本：{shown}。"
            "被问到这个应用本身、它运行在哪个软件里或它的版本号时，以此为准。")


class ChatPipeline:
    def __init__(self, user_id: str):
        # user_id 必填、不给默认值：默认成 "default_user" 就是本机管理员，漏传的
        # 调用点会静默把对话记到管理员名下、并以他的身份检索与写记忆——和本计划
        # 对 owner 禁默认值完全同一形状的洞。宁可直接 TypeError。
        self.user_id = user_id
        # 测试/CI 下为 None（走内存假存储），此时跳过记忆注入与自动保存
        self.memory = memory_manager
        # 每个请求新建一个 pipeline，所以这一行天然是"按当下的可用性重算"：
        # 探测值是模块级缓存，代价只是一次布尔读，不会每次都去连 socket。
        self.tools_schema = get_available_tools_schema()

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

        # 4. 按信号自动保存记忆（多数轮次什么都不存，那是设计意图）
        self.save_interaction(user_input)

        msg_id = str(uuid.uuid4())
        return {"reply": final_reply, "message_id": msg_id,
                "used_memory_ids": used_memory_ids}

    def inject_context(self, messages: List[Dict], query: str):
        """注入今天日期、记忆与该用户自己的偏好摘要（非流式与流式共用）。

        返回 (消息列表, 本次用到的记忆 id 列表)——后者用于反馈闭环，
        否则无法知道该给哪些记忆加权。
        """
        used_ids = []

        # 时间锚点排在最前，且走 _append_system：前端带着 persona 来时那条就是
        # messages[0]，这里既不许插出第二条 system，也不许把人家那段盖掉或挪到它前面。
        # 少这一句的代价线上付过一次：没有"今天"当锚点，"2027 年简章"这种问题模型
        # 既换算不出年份，也无从判断该不该去搜。
        self._append_system(messages, _today_line())
        # 环境锚点紧跟日期：它和记忆/偏好一样是"附加说明"，不许挤掉日期那句的
        # 首位（test_today_context 锁的就是首段必须是"今天是 …"）。
        self._append_system(messages, _env_line())

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
            # 读的是**这个调用者自己**那份偏好摘要。偏好原先是一份全站共享的
            # preference.txt，任何一个人点 👎 都会改写所有人下一轮的语气；
            # 现在按人分账（见 preference_analyzer.preference_path）。
            preference = read_preference(self.user_id)
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
        billed = {k: 0 for k in ("prompt_tokens", "completion_tokens",
                                 "total_tokens", "reasoning_tokens", "cached_tokens")}
        rounds = 0
        ok = True
        try:

            # 复制消息列表，避免修改原始数据
            msgs = list(messages)

            for turn in range(max_turns):
                kwargs = {"model": provider["model"], "messages": msgs}
                if self.tools_schema:
                    # 空数组不传：有些兼容网关对 tools=[] 直接 400。流式那条
                    # (core/streaming.py) 一直是对的，两侧此前不一致——
                    # 症状是"界面配得上、非流式接口 400"，只在工具全被禁用时出现。
                    kwargs["tools"] = self.tools_schema
                    kwargs["tool_choice"] = "auto"
                response = client.chat.completions.create(**kwargs)
                rounds += 1
                u = getattr(response, "usage", None)
                if u is not None:
                    billed["prompt_tokens"] += getattr(u, "prompt_tokens", 0) or 0
                    billed["completion_tokens"] += getattr(u, "completion_tokens", 0) or 0
                    billed["total_tokens"] += getattr(u, "total_tokens", 0) or 0
                    details = getattr(u, "completion_tokens_details", None)
                    billed["reasoning_tokens"] += (getattr(details, "reasoning_tokens", 0) or 0) if details else 0
                    pdetails = getattr(u, "prompt_tokens_details", None)
                    billed["cached_tokens"] += (getattr(pdetails, "cached_tokens", 0) or 0) if pdetails else 0
                msg = response.choices[0].message

                if msg.tool_calls:
                    # 执行工具，并将结果追加回消息
                    msgs.append(msg.model_dump())
                    for tool_call in msg.tool_calls:
                        name = tool_call.function.name
                        # 模型吐出残缺 JSON 参数很常见，这不该炸掉整轮对话：解析
                        # 错误按"工具结果"回填，让模型自己重试或换路——原来这行在
                        # 内层 try 之外，一次坏参数 = /v1/chat 502，本轮全丢。
                        try:
                            args = json.loads(tool_call.function.arguments)
                        except (ValueError, TypeError) as e:
                            msgs.append({
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "content": f"工具参数解析失败（不是合法 JSON）: {e}",
                            })
                            continue
                        print(f"[Pipeline] 调用工具: {name}({args})")
                        try:
                            result = execute_tool(name, args, user_id=self.user_id)
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
        except BaseException:
            ok = False
            raise
        finally:
            # 与流式那一条同一个口径：记账用上游回传的数，上游没回就记 0 并留
            # unknown_usage；这一路抛出去异常也要落一行 failed，否则"只对一半人"的
            # 账比没账更坏——它会让人以为失败是免费的。
            usage.record_call(user_id=self.user_id or "unattributed",
                              provider_id=provider["id"],
                              paid_by=provider.get("paid_by") or "operator",
                              tool_rounds=max(0, rounds - 1), ok=ok, **billed)

    def save_interaction(self, user_input: str):
        """按信号存记忆：只有这句话值得长期记住，才进库。

        判据在 `app/memory/signals.py` 那一个地方。AI 的回答不再进摘要——那是模型
        说过的话，不是关于这个人的事实；会话历史里本来就有全文，把它截 100 字塞进
        向量库只制造检索噪声。
        """
        if self.memory is None:
            return
        signal = signals.classify(user_input)
        if signal is None:
            return
        try:
            hits = self.memory.search_memory(self.user_id, signal.quote, top_k=3)
            if signals.is_repeat(signal.quote, [doc for doc, _, _ in hits]):
                return
            self.memory.add_memory(self.user_id, signal.quote,
                                   metadata={"kind": signal.kind, "scope": signal.scope})
        except Exception as e:
            print(f"记忆保存失败: {e}")