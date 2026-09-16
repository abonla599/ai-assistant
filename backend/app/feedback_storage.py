import json
import os

from app.core.paths import data_root

# 必须是绝对路径：裸相对名会随进程工作目录漂移。打包版入口 chdir 到 EXE 目录，
# 而该目录每次重建都被清空，等于把反馈数据写进一个注定消失的地方。
FEEDBACK_FILE = os.path.join(data_root(), "feedback.json")

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