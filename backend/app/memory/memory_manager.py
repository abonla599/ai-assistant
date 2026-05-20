import os
import uuid
import chromadb
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# 禁用 chromadb 遥测，避免 CI 中报错干扰
os.environ["ANONYMIZED_TELEMETRY"] = "False"


class MemoryManager:
    """记忆管理器：负责存储和检索用户记忆"""

    def __init__(self, collection_name="user_memories", persist_dir="./chroma_db"):
        self.use_local_embed = False
        api_key = os.getenv("api_key")

        if api_key:
            try:
                self.client = OpenAI(
                    api_key=api_key,
                    base_url="https://api.apiyi.com/v1"
                )
                self.embed_model = "text-embedding-3-small"
                # 验证 API 是否可用
                self.client.embeddings.create(model=self.embed_model, input=["test"])
                print("✅ 使用 OpenAI 嵌入模型")
            except Exception as e:
                print(f"⚠️ OpenAI 嵌入初始化失败: {e}，降级为本地嵌入模型")
                self._init_local_embed()
        else:
            print("⚠️ 未配置 api_key，降级为本地嵌入模型")
            self._init_local_embed()

        self.chroma_client = chromadb.PersistentClient(path=persist_dir)
        self.collection = self.chroma_client.get_or_create_collection(name=collection_name)
        print(f"✅ MemoryManager 初始化完成 (集合: {collection_name}, 嵌入方式: {'本地' if self.use_local_embed else '云端'})")

    def _init_local_embed(self):
        """初始化本地嵌入模型（无需 API key）"""
        from sentence_transformers import SentenceTransformer
        # all-MiniLM-L6-v2 是轻量模型，适合 CI 环境
        self.local_model = SentenceTransformer('all-MiniLM-L6-v2')
        self.embed_model = "local"
        self.use_local_embed = True

    def _embed(self, text: str) -> list:
        if self.use_local_embed:
            return self.local_model.encode(text).tolist()
        else:
            response = self.client.embeddings.create(
                model=self.embed_model,
                input=[text]
            )
            return response.data[0].embedding

    def _summarize(self, text: str, max_length: int = 100) -> str:
        if self.use_local_embed:
            return text[:max_length]  # 本地模式不支持摘要，直接截断
        if len(text) <= max_length:
            return text
        try:
            response = self.client.chat.completions.create(
                model="gpt-3.5-turbo",
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
        if summarize and not self.use_local_embed:
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

    # 以下方法保持不变（你原有代码）
    def search_memory(self, user_id: str, query: str, top_k: int = 5) -> list:
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
        try:
            self.collection.delete(ids=[memory_id])
            print(f"🗑️ 记忆已删除: {memory_id}")
            return True
        except Exception as e:
            print(f"❌ 删除记忆失败: {e}")
            return False

    def delete_memories_batch(self, memory_ids: list) -> dict:
        try:
            self.collection.delete(ids=memory_ids)
            print(f"🗑️ 已批量删除 {len(memory_ids)} 条记忆")
            return {"status": "deleted", "count": len(memory_ids)}
        except Exception as e:
            return {"error": str(e)}

    def update_memory(self, memory_id: str, new_content: str = None,
                      new_weight: float = None) -> dict:
        data = self.collection.get(ids=[memory_id])
        if not data['ids']:
            return {"error": "记忆不存在"}

        doc = new_content if new_content else data['documents'][0]
        meta = data['metadatas'][0]
        if new_weight is not None:
            meta['weight'] = new_weight

        if new_content:
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
        count = self.collection.count()
        return {
            "collection_name": self.collection.name,
            "total_memories": count
        }