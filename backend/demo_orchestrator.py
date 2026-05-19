"""
智能体演示脚本
演示一个完整的复杂任务执行流程：
"查找近日 AI 领域的重要新闻，总结成三个要点，用 Python 脚本保存到 news.txt"
"""
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(__file__))

from app.agents.orchestrator import Orchestrator

DEMO_TASK = "请帮我查找近日 AI 领域的重要新闻，总结成三个要点，并用 Python 写一个简短的脚本，将这三个要点保存到本地文件 news.txt 中。"


def main():
    print("=" * 60)
    print("  智能体演示 - 复杂任务执行")
    print("=" * 60)
    print(f"\n📋 任务描述：\n{DEMO_TASK}\n")
    print("-" * 60)

    # 创建协调器，使用 DeepSeek 模型
    orch = Orchestrator(model="deepseek-chat")

    print("🚀 开始执行任务...\n")

    result = orch.run(DEMO_TASK)

    print("\n" + "=" * 60)
    print("  执行结果")
    print("=" * 60)

    if result["status"] == "completed":
        print(f"\n✅ 任务状态：已完成")
        print(f"\n📝 执行计划：")
        for i, step in enumerate(result.get("subtasks", []), 1):
            print(f"  {i}. {step}")

        print(f"\n📊 各步骤结果：")
        for i, r in enumerate(result.get("results", [])):
            print(f"\n  步骤 {i+1}")
            print(f"  {str(r)[:300]}...")

        print(f"\n📄 最终汇总：")
        print(result.get("final_answer", "无汇总"))

        # 检查 news.txt 是否创建
        news_path = os.path.join(os.path.dirname(__file__), "news.txt")
        if os.path.exists(news_path):
            print(f"\n📁 news.txt 已创建，内容如下：")
            with open(news_path, "r", encoding="utf-8") as f:
                print(f.read())
        else:
            print(f"\n⚠️ news.txt 未在 backend/ 目录下找到")

    elif result["status"] == "cancelled":
        print(f"\n⚠️ 任务被取消")
        print(f"  已完成 {result.get('completed_subtasks', 0)}/{result.get('total_subtasks', 0)} 步")

    else:
        print(f"\n❌ 任务失败：{result.get('error', '未知错误')}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()