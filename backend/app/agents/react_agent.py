import json
from app.agents.base_agent import BaseAgent

class ReActAgent(BaseAgent):
    def __init__(self, name, model="deepseek-chat", tools_schema=None, system_prompt=None):
        super().__init__(name, {})
        self.model = model
        self.tools_schema = tools_schema or []
        self.system_prompt = system_prompt or "你是一个智能助手，可以使用工具完成任务。请一步步思考。"
        self.tools = {}

    def _build_messages(self, task: str, history: list = None):
        msgs = [{"role": "system", "content": self.system_prompt}]
        if history:
            msgs.extend(history)
        return msgs

    def run(self, task: str, context: list = None) -> str:
        messages = self._build_messages(task, context)
        messages.append({"role": "user", "content": task})
        
        max_turns = 10
        for turn in range(max_turns):
            print("=" * 20)
            print(f"第 {turn+1} 轮思考")
            print("最近消息:", messages[-3:])
            
            response = self._call_llm_with_tools(messages)
            msg = response.choices[0].message

            if msg.tool_calls:
                messages.append(msg.model_dump())
                for tool_call in msg.tool_calls:
                    name = tool_call.function.name
                    arguments = json.loads(tool_call.function.arguments)
                    print(f"调用工具: {name}  参数: {arguments}")
                    
                    if name in self.tools:
                        tool_func = self.tools[name]["function"]
                        try:
                            output = tool_func(**arguments)
                        except Exception as e:
                            output = f"工具执行错误: {e}"
                    else:
                        output = f"工具 {name} 不存在。"
                    
                    print(f"工具结果: {output}")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": str(output)
                    })
            else:
                final_answer = msg.content
                print("Agent 最终回答:", final_answer)
                return final_answer
        
        return "Agent 达到最大循环次数，未得出最终答案。"

    def _call_llm_with_tools(self, messages):
        from openai import OpenAI
        import os
        from dotenv import load_dotenv
        load_dotenv()
        
        model_configs = {
            "deepseek-chat": ("DEEPSEEK_API_KEY", "https://api.deepseek.com/v1", "deepseek-chat"),
            "gpt-3.5-turbo": ("OPENAI_API_KEY", "https://api.openai.com/v1", "gpt-3.5-turbo"),
        }
        key_env, base, model_name = model_configs.get(self.model, model_configs["deepseek-chat"])
        api_key = os.getenv(key_env)
        client = OpenAI(api_key=api_key, base_url=base)
        return client.chat.completions.create(
            model=model_name,
            messages=messages,
            tools=self.tools_schema
        )