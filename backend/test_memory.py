"""
记忆管理模块测试脚本
运行：python test_memory.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from app.memory.memory_manager import MemoryManager


def main():
    print("=" * 60)
    print("记忆管理模块测试")
    print("=" * 60)
    
    # 初始化
    mm = MemoryManager()
    print("\n--- 测试添加记忆 ---")
    
    # 添加记忆
    mem1 = mm.add_memory("user_001", "我叫张三，今年25岁", {"category": "personal_info"})
    print(f"  ID: {mem1}")
    
    mem2 = mm.add_memory("user_001", "我喜欢爬山和游泳", {"category": "hobby"})
    print(f"  ID: {mem2}")
    
    mem3 = mm.add_memory("user_001", "我在北京工作，是一名程序员", {"category": "work"})
    print(f"  ID: {mem3}")
    
    mem4 = mm.add_memory("user_002", "我叫李四，喜欢读书", {"category": "personal_info"})
    print(f"  ID: {mem4}")
    
    # 测试搜索
    print("\n--- 测试搜索记忆 ---")
    results = mm.search_memory("user_001", "这个人喜欢什么运动", top_k=2)
    for i, (content, dist, meta) in enumerate(results):
        print(f"  结果{i+1}: {content} (距离: {dist:.4f})")
    
    # 测试获取用户所有记忆
    print("\n--- 用户 user_001 的所有记忆 ---")
    memories = mm.get_user_memories("user_001")
    for mem in memories:
        print(f"  - [{mem['metadata'].get('category', 'unknown')}] {mem['content']}")
    
    # 统计信息
    print("\n--- 统计信息 ---")
    stats = mm.get_collection_stats()
    print(f"  集合名称: {stats['collection_name']}")
    print(f"  记忆总数: {stats['total_memories']}")
    
    print("\n✅ 所有测试完成")


if __name__ == "__main__":
    main()