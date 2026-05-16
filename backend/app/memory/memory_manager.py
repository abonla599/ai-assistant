import os
import uuid
import chromadb
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


class MemoryManager:
    """记忆管理器：负责存储和检索用户记忆"""

    def __init__(self, collection_name="user_memories", persist_dir="./chroma_db"):
        # 使用你的代理服务初始化 OpenAI 客户端
        self.client = OpenAI(
            api_key=os.getenv("api_key"),
            base_url="https://api.apiyi.com/v1"
        )
        
        # 初始化 Chroma 持久化客户端
        self.chroma_client = chromadb.PersistentClient(path=persist_dir)
        
        # 获取或创建集合
        self.collection = self.chroma_client.get_or_create_collection(name=collection_name)
        
        # 嵌入模型
        self.embed_model = "text-embedding-3-small"
        
        print(f"✅ MemoryManager 初始化完成 (集合: {collection_name}, 模型: {self.embed_model})")

    def _embed(self, text: str) -> list:
        """将文本转换为嵌入向量"""
        response = self.client.embeddings.create(
            model=self.embed_model,
            input=[text]
        )
        return response.data[0].embedding

    def add_memory(self, user_id: str, content: str, metadata: dict = None) -> str:
        """
        添加一条记忆到向量数据库
        
        参数:
            user_id: 用户ID
            content: 记忆内容
            metadata: 额外的元数据（如情感、权重等）
        
        返回:
            记忆ID
        """
        # 生成嵌入向量
        embedding = self._embed(content)
        
        # 生成唯一记忆ID
        mem_id = str(uuid.uuid4())
        
        # 准备元数据
        meta = {
            "user_id": user_id,
            "weight": 1.0,
            **(metadata or {})
        }
        
        # 添加到集合
        self.collection.add(
            documents=[content],
            embeddings=[embedding],
            metadatas=[meta],
            ids=[mem_id]
        )
        
        print(f"📝 记忆已添加: [{user_id}] {content[:50]}...")
        return mem_id

    def search_memory(self, user_id: str, query: str, top_k: int = 5) -> list:
        """
        语义搜索相关记忆
        
        参数:
            user_id: 用户ID
            query: 查询文本
            top_k: 返回的最相关记忆数量
        
        返回:
            [(内容, 距离, 元数据), ...]
        """
        # 生成查询向量
        query_embed = self._embed(query)
        
        # 从 Chroma 检索（多取一些用于过滤）
        results = self.collection.query(
            query_embeddings=[query_embed],
            n_results=top_k * 2
        )
        
        # 按 user_id 过滤
        filtered = []
        
        if results['ids'] and results['ids'][0]:
            for i, mem_id in enumerate(results['ids'][0]):
                meta = results['metadatas'][0][i] if results['metadatas'] else {}
                
                # 优先返回匹配 user_id 的记忆
                if meta.get("user_id") == user_id:
                    filtered.append((
                        results['documents'][0][i],
                        results['distances'][0][i],
                        meta
                    ))
        
        # 如果没有匹配 user_id 的，返回所有结果（宽松策略）
        if not filtered and results['ids'] and results['ids'][0]:
            for i, mem_id in enumerate(results['ids'][0]):
                meta = results['metadatas'][0][i] if results['metadatas'] else {}
                filtered.append((
                    results['documents'][0][i],
                    results['distances'][0][i],
                    meta
                ))
        
        return filtered[:top_k]

    def delete_memory(self, memory_id: str) -> bool:
        """删除一条记忆"""
        try:
            self.collection.delete(ids=[memory_id])
            print(f"🗑️ 记忆已删除: {memory_id}")
            return True
        except Exception as e:
            print(f"❌ 删除记忆失败: {e}")
            return False

    def get_user_memories(self, user_id: str, limit: int = 20) -> list:
        """获取用户的所有记忆"""
        try:
            all_data = self.collection.get()
            
            user_memories = []
            if all_data['ids']:
                for i, mem_id in enumerate(all_data['ids']):
                    meta = all_data['metadatas'][i] if all_data['metadatas'] else {}
                    if meta.get("user_id") == user_id:
                        user_memories.append({
                            "id": mem_id,
                            "content": all_data['documents'][i],
                            "metadata": meta
                        })
            
            return user_memories[:limit]
        except Exception as e:
            print(f"❌ 获取用户记忆失败: {e}")
            return []

    def get_collection_stats(self) -> dict:
        """获取集合统计信息"""
        count = self.collection.count()
        return {
            "collection_name": self.collection.name,
            "total_memories": count
        }