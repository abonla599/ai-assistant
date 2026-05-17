import json
import os
from datetime import datetime

# --- 配置文件 ---
FEEDBACK_FILE = "feedback.json"       # 存储用户反馈的文件
PREFERENCE_FILE = "preference.txt"    # 我们最终要生成的，存放用户偏好的文件

# --- 核心分析函数 ---
def analyze_and_update_preference():
    """
    1. 读取所有用户反馈 (feedback.json)
    2. 对反馈进行简单的分析和总结
    3. 将分析结果写入 preference.txt
    """
    print(f"\n[{datetime.now()}] 开始分析用户反馈...")
    
    # 步骤 1: 检查反馈文件是否存在
    if not os.path.exists(FEEDBACK_FILE):
        print("尚未找到 feedback.json，稍后再试。")
        return
    
    # 步骤 2: 加载所有反馈数据
    with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
        feedbacks = json.load(f)
    
    if not feedbacks:
        print("还没有任何反馈数据，跳过分析。")
        return
    
    # 步骤 3: 统计分析 - 点赞与点踩的计数
    like_count = 0
    dislike_count = 0
    for item in feedbacks:
        if item.get("rating") == 1:
            like_count += 1
        elif item.get("rating") == 0:
            dislike_count += 1
    
    # 步骤 4: 生成用户偏好摘要（这里的逻辑可以随项目发展不断优化）
    preference_summary = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]\n"
    preference_summary += f"根据对 {len(feedbacks)} 条反馈的分析:\n"
    preference_summary += f"👍 用户表示了 {like_count} 次满意。\n"
    preference_summary += f"👎 用户表示了 {dislike_count} 次不满意。\n"
    
    # 添加更智能的洞察（作为未来的扩展点）
    preference_summary += "\n💡 总结: "
    if like_count > dislike_count:
        preference_summary += "用户整体反馈积极，可以继续提供简洁、准确的回答。\n"
    elif dislike_count > like_count:
        preference_summary += "用户整体反馈消极，可能需要调整回答风格，例如提供更多细节或更清晰的解释。\n"
    else:
        preference_summary += "用户反馈不明确，建议保持中立和友善的沟通方式。\n"
    
    # 步骤 5: 将生成的摘要写入文件
    with open(PREFERENCE_FILE, "w", encoding="utf-8") as f:
        f.write(preference_summary)
    
    print(f"✅ 分析完成！已生成用户偏好摘要，并保存至 {PREFERENCE_FILE}")
    print(preference_summary)


# --- 用于独立测试的部分 ---
if __name__ == "__main__":
    analyze_and_update_preference()
    print("\n[提示] 若需定期自动运行，请参考项目主文件中的集成方法。")