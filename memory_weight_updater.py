import json
import os
import chromadb
from chromadb.utils import embedding_functions
from datetime import datetime

# 1. 配置文件路径
FEEDBACK_FILE = "feedback.json"
DATA_DIR = "./data"
COLLECTION_NAME = "user_memories"

# 2. 连接Chroma数据库，准备操作
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)
chroma_client = chromadb.PersistentClient(path=DATA_DIR)

# 尝试获取集合，若不存在则创建
try:
    collection = chroma_client.get_collection(COLLECTION_NAME)
except:
    # 内存收集需要一个embedding函数，这里用最简单的默认函数
    embedding_fn = embedding_functions.DefaultEmbeddingFunction()
    collection = chroma_client.create_collection(name=COLLECTION_NAME, embedding_function=embedding_fn)


def update_memory_weights_from_feedback():
    """
    1. 读取所有用户反馈 (feedback.json)
    2. 按消息ID分组，统计点赞和点踩次数
    3. 根据统计结果，更新Chroma中对应记忆的权重分数
    """
    print(f"\n[{datetime.now()}] 开始根据反馈更新记忆权重...")

    # 读取反馈数据
    if not os.path.exists(FEEDBACK_FILE):
        print("  反馈文件 feedback.json 不存在，跳过权重更新。")
        return
    with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
        feedbacks = json.load(f)
    if not feedbacks:
        print("  暂无反馈数据，跳过权重更新。")
        return

    # 按 message_id 聚合反馈，计算净得分
    memory_scores = {}
    for fb in feedbacks:
        msg_id = fb.get("message_id")
        rating = fb.get("rating")
        if msg_id is None or rating is None:
            continue
        # 点赞 (+1) / 点踩 (-1)
        delta = 1 if rating == 1 else -1
        memory_scores[msg_id] = memory_scores.get(msg_id, 0) + delta

    # 获取Chroma中所有已存储的文档
    all_memories = collection.get()
    if not all_memories['ids']:
        print("  向量数据库中暂无记忆文档，跳过更新。")
        return

    print(f"  正在更新 {len(all_memories['ids'])} 条记忆的权重...")
    update_count = 0
    # 批量更新文档的元数据
    for idx, mem_id in enumerate(all_memories['ids']):
        # 获取该记忆当前的元数据
        current_metadata = all_memories['metadatas'][idx] or {}
        # 获取当前权重，默认为0
        current_weight = current_metadata.get('weight', 0.0)
        
        # 如果这条记忆有反馈记录，就调整权重
        if mem_id in memory_scores:
            new_weight = current_weight + memory_scores[mem_id]
            # 限制权重范围在 [-5, 5] 之间
            new_weight = max(-5.0, min(5.0, new_weight))
            print(f"    记忆 [{mem_id}] 权重调整：{current_weight:.1f} → {new_weight:.1f}")
            update_count += 1
        else:
            # 没有反馈，可添加轻微的时间衰减，保持权重向0靠近
            new_weight = current_weight * 0.99
        # 更新元数据
        collection.update(ids=[mem_id], metadatas=[{"weight": new_weight}])
    print(f"✅ 记忆权重更新完成！共更新了 {update_count} 条记忆。")


# 测试入口
if __name__ == "__main__":
    update_memory_weights_from_feedback()