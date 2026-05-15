from app.core.llm_client import get_llm_response

def main():
    print("AI 智能助手 - 命令行版")
    print("输入 'quit' 退出，输入 'model:deepseek-chat' 切换模型")
    print("-" * 50)

    current_model = "deepseek-chat"
    messages = []  # 当前对话历史

    while True:
        user_input = input("你: ")
        if user_input.lower() == "quit":
            print("再见！")
            break
        elif user_input.startswith("model:"):
            new_model = user_input.split(":", 1)[1].strip()
            if new_model in ["deepseek-chat", "gpt-4o", "gpt-3.5-turbo"]:
                current_model = new_model
                print(f"已切换到模型: {current_model}")
            else:
                print("不支持的模型，可用: deepseek-chat, gpt-4o, gpt-3.5-turbo")
            continue

        messages.append({"role": "user", "content": user_input})
        try:
            reply = get_llm_response(current_model, messages)
            print(f"AI [{current_model}]: {reply}")
            messages.append({"role": "assistant", "content": reply})
        except Exception as e:
            print(f"错误: {e}")

if __name__ == "__main__":
    main()