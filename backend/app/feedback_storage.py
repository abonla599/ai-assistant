import json
import os

from app.core.paths import data_root

# 必须是绝对路径：裸相对名会随进程工作目录漂移。打包版入口 chdir 到 EXE 目录，
# 而该目录每次重建都被清空，等于把反馈数据写进一个注定消失的地方。
FEEDBACK_FILE = os.path.join(data_root(), "feedback.json")

# 修改函数入参，直接接收独立参数
def save_feedback(message_id: str, rating: int, comment: str, user_id: str):
    """记下一条反馈，并记下是谁给的。

    user_id 必填、无默认值：与 sessions/uploads 的 owner 同一口径。没有默认身份
    可退，才不会出现"漏传的调用点把反馈记到管理员名下"这种静默错位；而调用方
    必须先证明这条 message_id 属于他（见 main.py 的 /v1/feedback），否则一行都
    不该写进来。
    """
    feedback_data = {
        "message_id": message_id,
        "rating": rating,
        "comment": comment,
        "user_id": user_id
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