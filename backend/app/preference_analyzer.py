import hashlib
import json
import os
import re
from datetime import datetime

from app.core.paths import data_root
from app.feedback_storage import FEEDBACK_FILE

# --- 配置文件 ---
# 反馈路径只有一个定义处（feedback_storage），避免两边写到不同文件
PREFERENCE_FILE = os.path.join(data_root(), "preference.txt")    # 本机管理员的那一份偏好摘要

# 与 authz.BOOTSTRAP_PRINCIPAL、session_store.LEGACY_OWNER 同一个身份。
# 按人分账之前的反馈行没有 user_id，那时人人都是本机管理员，所以这些历史行
# 仍算在他名下，他的摘要也继续用老文件名 preference.txt —— 改名等于把他多年
# 点出来的那份汇总白扔掉。
LEGACY_USER_ID = "default_user"

# 用户名要进文件名，就必须先剥掉路径语义：分隔符、".."、绝对路径都只可能是
# 别人递进来的 user_id（注册名不受控），不能让它把文件写到 data 目录之外。
_PATH_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def preference_path(user_id: str) -> str:
    """该用户自己那份偏好摘要的路径。

    刻意不给"全局那一份"留出口：偏好曾经只有 preference.txt 一个文件，所有人都
    被注入同一份汇总，于是任何人点一次 👎 就改写了全站下一轮的回答风格。
    """
    if not user_id:
        raise ValueError("偏好摘要必须有归属：不传 user_id 就等于回到全站共享那一份")
    if user_id == LEGACY_USER_ID:
        return PREFERENCE_FILE
    stem, ext = os.path.splitext(os.path.basename(PREFERENCE_FILE))
    wanted = str(user_id)
    slug = _PATH_UNSAFE.sub("", wanted)
    if slug != wanted:
        # 剥过字符的文件名不再唯一："a/b" 与 "ab" 会撞成同一个文件，
        # 而那是两个不同的人互相改写对方的摘要。身份串里只要有害字符，
        # 就整条退回稳定哈希：名字短一点没关系，撞车不行。
        slug = hashlib.sha256(wanted.encode("utf-8")).hexdigest()[:16]
    return os.path.join(os.path.dirname(PREFERENCE_FILE), f"{stem}-{slug}{ext}")


def _all_feedback_rows() -> list:
    """反馈原文。文件不存在就返回空列表，与原实现一致。"""
    if not os.path.exists(FEEDBACK_FILE):
        return []
    with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
        feedbacks = json.load(f)
    return feedbacks or []


def _rows_of(user_id: str) -> list:
    """只属于这个人的反馈行。

    历史行没有 user_id 键：那些写入发生在"人人都是 default_user"的年代，
    归到他名下，与 preference_path 沿用老文件名是同一个约定。
    """
    return [item for item in _all_feedback_rows()
            if (item.get("user_id") or LEGACY_USER_ID) == user_id]


def users_with_feedback() -> list:
    """反馈里出现过哪些人（保持首次出现的顺序），供后台定时器逐个重算。"""
    seen = []
    for item in _all_feedback_rows():
        owner = item.get("user_id") or LEGACY_USER_ID
        if owner not in seen:
            seen.append(owner)
    return seen


# --- 核心分析函数 ---
def analyze_and_update_preference(user_id: str):
    """把**该用户自己**的反馈汇总成一份偏好摘要，写进他那一份文件。

    原先这个函数不带身份：它把所有人的反馈合并统计后写入唯一的 preference.txt，
    而那份文件被注入**每个人**的 system 提示——一个人点 👎，所有人的回答风格
    跟着变，而且每来一条反馈、每 300 秒都重算一次。反馈行早在上一轮就记了
    user_id（那只是数据前提），这一层才把聚合与落盘真正分账。
    """
    print(f"\n[{datetime.now()}] 开始分析用户 {user_id} 的反馈...")

    if not os.path.exists(FEEDBACK_FILE):
        print("尚未找到 feedback.json，稍后再试。")
        return

    # 步骤 3: 统计分析 - 点赞与点踩的计数（只统计他自己的反馈行）
    feedbacks = _rows_of(user_id)
    if not feedbacks:
        print(f"用户 {user_id} 还没有任何反馈，跳过分析。")
        return

    like_count = 0
    dislike_count = 0
    for item in feedbacks:
        try:
            rating = int(item.get("rating", 0))
        except (TypeError, ValueError):
            continue
        # 约定 👍=1、👎=-1；旧数据里点踩曾记为 0，一并算作不满意。
        if rating > 0:
            like_count += 1
        elif rating <= 0:
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

    # 步骤 5: 将生成的摘要写入**他自己**那份文件
    path = preference_path(user_id)
    with open(path, "w", encoding="utf-8") as f:
        f.write(preference_summary)

    print(f"✅ 分析完成！已生成用户 {user_id} 的偏好摘要，并保存至 {path}")
    print(preference_summary)


def analyze_all_preferences():
    """后台定时器入口：给"反馈里出现过的人"各算一份，不存在全站那一份。

    定时器没有当前调用者，所以它不能替某个人决定归属；有几个人的反馈就算几份，
    谁的那一份也只由他自己的反馈决定。
    """
    for owner in users_with_feedback():
        analyze_and_update_preference(owner)


def read_preference(user_id: str) -> str:
    """读取该用户自己的偏好摘要，供对话链路注入 system 提示。

    身份必填：读的是"谁的"偏好就是问题本身，给个默认值等于回到共享那一份。
    读不到时返回空串，不影响正常对话。
    """
    path = preference_path(user_id)
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


# --- 用于独立测试的部分 ---
if __name__ == "__main__":
    analyze_all_preferences()
    print("\n[提示] 若需定期自动运行，请参考项目主文件中的集成方法。")
