import json
import os

FEEDBACK_FILE = "feedback.json"

def save_feedback(feedback_data):
    """保存反馈到 JSON 文件"""
    if os.path.exists(FEEDBACK_FILE):
        with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = []
    data.append(feedback_data)
    with open(FEEDBACK_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return True