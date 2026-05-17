import os
import uuid
import chromadb
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


class MemoryManager:
    """记忆管理器：负责存储和检索用户记忆"""

    def __init__(self, collection_name="user_memories", persist_dir="./chroma_db"):
        self.client = OpenAI(
            api_key=os.getenv("api_key"),
            base_url="https://api.apiyi.com/v1"
        )
        self.chroma_client = chromadb.PersistentClient(path=persist_dir)
        self.collection = self.chroma_client.get_or_create_collection(name=collection_name)
        self.embed_model = "text-embedding-3-small"
        print(f"✅ MemoryManager 初始化完成 (集合: {collection_name}, 模型: {self.embed_model})")

    def _embed(self, text: str) -> list:
        """将文本转换为嵌入向量"""
        response = self.client.embeddings.create(
            model=self.embed_model,
            input=[text]
        )
        return response.data[0].embedding

    def _summarize(self, text: str, max_length: int = 100) -> str:
        """用 LLM 把长文本压缩成一句话，失败时自动截断"""
        if len(text) <= max_length:
            return text
        try:
            response = self.client.chat.completions.create(
                model="gpt-3.5-turbo",   # 也可换成 deepseek-chat
                messages=[
                    {"role": "system", "content": "将以下内容压缩成一句话，只保留最重要的信息。"},
                    {"role": "user", "content": text}
                ],
                max_tokens=80,
                temperature=0.3
            )
            summary = response.choices[0].message.content.strip()
            return summary if summary else text[:max_length]
        except Exception as e:
            print(f"⚠️ 摘要失败，改用截断: {e}")
            return text[:max_length]

    def add_memory(self, user_id: str, content: str, metadata: dict = None,
                   summarize: bool = False) -> str:
        """
        添加一条记忆到向量数据库
        :param user_id: 用户ID
        :param content: 记忆内容
        :param metadata: 额外的元数据
        :param summarize: 是否用LLM压缩长文本（默认False则简单截断）
        :return: 记忆ID
        """
        # 内容长度控制：默认截断200字符，可开启AI摘要
        if summarize:
            content = self._summarize(content)
        else:
            content = content[:200]

        embedding = self._embed(content)
        mem_id = str(uuid.uuid4())

        meta = {
            "user_id": user_id,
            "weight": 1.0,
            **(metadata or {})
        }

        self.collection.add(
            documents=[content],
            embeddings=[embedding],
            metadatas=[meta],
            ids=[mem_id]
        )

        print(f"📝 记忆已添加: [{user_id}] {content[:50]}...")
        return mem_id

    def search_memory(self, user_id: str, query: str, top_k: int = 5) -> list:
        """语义搜索相关记忆，返回 [(内容, 距离, 元数据), ...]"""
        query_embed = self._embed(query)
        results = self.collection.query(
            query_embeddings=[query_embed],
            n_results=top_k * 2
        )

        filtered = []
        if results['ids'] and results['ids'][0]:
            for i, mem_id in enumerate(results['ids'][0]):
                meta = results['metadatas'][0][i] if results['metadatas'] else {}
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

    def delete_memories_batch(self, memory_ids: list) -> dict:
        """批量删除记忆，返回操作结果"""
        try:
            self.collection.delete(ids=memory_ids)
            print(f"🗑️ 已批量删除 {len(memory_ids)} 条记忆")
            return {"status": "deleted", "count": len(memory_ids)}
        except Exception as e:
            return {"error": str(e)}

    def update_memory(self, memory_id: str, new_content: str = None,
                      new_weight: float = None) -> dict:
        """更新一条记忆的内容或权重"""
        data = self.collection.get(ids=[memory_id])
        if not data['ids']:
            return {"error": "记忆不存在"}

        doc = new_content if new_content else data['documents'][0]
        meta = data['metadatas'][0]
        if new_weight is not None:
            meta['weight'] = new_weight

        if new_content:
            # 内容变了，需要重新生成 embedding
            new_emb = self._embed(new_content)
            self.collection.update(
                ids=[memory_id],
                documents=[doc],
                embeddings=[new_emb],
                metadatas=[meta]
            )
        else:
            self.collection.update(
                ids=[memory_id],
                documents=[doc],
                metadatas=[meta]
            )
        return {"status": "updated"}

    def decay_weights(self, user_id: str, decay_factor: float = 0.95):
        """衰减指定用户所有记忆的权重，模拟遗忘曲线"""
        all_data = self.collection.get()
        ids_to_update = []
        new_metadatas = []

        for i, meta in enumerate(all_data['metadatas']):
            if meta and meta.get('user_id') == user_id:
                new_weight = meta.get('weight', 1.0) * decay_factor
                meta['weight'] = new_weight
                ids_to_update.append(all_data['ids'][i])
                new_metadatas.append(meta)

        if ids_to_update:
            self.collection.update(ids=ids_to_update, metadatas=new_metadatas)
            print(f"🧠 已衰减 {len(ids_to_update)} 条记忆的权重")
        else:
            print(f"⚠️ 未找到用户 {user_id} 的记忆")

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