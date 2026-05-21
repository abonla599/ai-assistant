import json
import os

FEEDBACK_FILE = "feedback.json"

# 修改函数入参，直接接收3个独立参数
def save_feedback(message_id: str, rating: int, comment: str):
    # 自动打包成字典，沿用你原来的保存逻辑
    feedback_data = {
        "message_id": message_id,
        "rating": rating,
        "comment": comment
    }

    if os.path.exists(FEEDBACK_FILE):
        with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = []

    data.append(feedback_data)

    with open(FEEDBACK_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return True