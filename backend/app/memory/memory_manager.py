import os
import sys
import uuid
import traceback
import chromadb
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# 禁用 chromadb 遥测，避免 CI 中报错干扰
os.environ["ANONYMIZED_TELEMETRY"] = "False"


def _default_persist_dir() -> str:
    """记忆库位置的唯一事实来源，避免依赖进程工作目录。

    优先级：CHROMA_DB_PATH 环境变量 > 打包后 EXE 同级目录 > 仓库根目录。
    """
    env_path = os.getenv("CHROMA_DB_PATH")
    if env_path:
        return os.path.abspath(env_path)
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), "chroma_db")
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    return os.path.join(repo_root, "chroma_db")


class MemoryManager:
    """记忆管理器：负责存储和检索用户记忆"""

    def __init__(self, collection_name="user_memories", persist_dir=None):
        self.persist_dir = os.path.abspath(persist_dir or _default_persist_dir())
        try:
            self.use_local_embed = False
            self._dummy_embed = False   # 伪嵌入标记
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
                    print(f"⚠️ OpenAI 嵌入不可用: {e}，尝试本地模型")
                    self._init_local_embed()
            else:
                print("⚠️ 未配置 api_key，尝试本地模型")
                self._init_local_embed()

            if self._dummy_embed:
                print("⚠️ 记忆检索已降级为伪嵌入（全零向量），语义检索结果不可信。"
                      "请配置 api_key 或安装 sentence-transformers。")

            self.chroma_client = chromadb.PersistentClient(path=self.persist_dir)
            self.collection = self._open_collection(collection_name)

            mode = "云端" if not (self.use_local_embed or self._dummy_embed) else ("本地" if self.use_local_embed else "伪嵌入")
            print(f"✅ MemoryManager 初始化完成 (集合: {collection_name}, 嵌入方式: {mode}, "
                  f"维度: {self.embed_dim}, 库路径: {self.persist_dir})")
        except Exception as e:
            print("❌ MemoryManager 初始化失败，详细异常如下：")
            traceback.print_exc()
            raise e

    def _open_collection(self, name: str):
        """打开 collection，并校验它记录的嵌入后端与当前后端是否一致。

        ChromaDB 在首次写入时按当次向量锁定维度，之后切换嵌入后端只会在
        add/query 时抛出难以定位的 InvalidArgumentError，因此提前到启动阶段
        给出明确的冲突原因和处理方式。
        """
        self.embed_dim = len(self._embed("__dimension_probe__"))
        # hnsw:space 仅在集合创建时生效，必须在此处声明；
        # 默认 L2 距离会让 1-distance 的相关度公式算出负值。
        expected = {
            "embedding_model": self.embed_model,
            "embedding_dim": str(self.embed_dim),
            "hnsw:space": "cosine",
        }

        collection = self.chroma_client.get_or_create_collection(name=name, metadata=expected)
        stored = collection.metadata or {}
        stored_dim = stored.get("embedding_dim")
        stored_model = stored.get("embedding_model")

        if stored_dim and str(stored_dim) != str(self.embed_dim):
            raise RuntimeError(
                f"记忆库嵌入维度冲突：collection '{name}' 以 {stored_dim} 维"
                f"（模型 {stored_model or '未知'}）建立，当前后端返回 {self.embed_dim} 维"
                f"（模型 {self.embed_model}）。请固定使用同一嵌入后端，"
                f"或删除 {self.persist_dir} 重建（会丢失已有记忆）。"
            )
        if stored_model and stored_model != self.embed_model:
            raise RuntimeError(
                f"记忆库嵌入模型冲突：collection '{name}' 由 {stored_model} 建立，当前为 "
                f"{self.embed_model}。两者维度相同但向量空间不通用，混用会让检索结果失真，"
                f"请固定使用同一嵌入后端，或删除 {self.persist_dir} 重建（会丢失已有记忆）。"
            )
        if not stored_model or not stored_dim:
            try:
                collection.modify(metadata=expected)
            except Exception as e:
                print(f"⚠️ 无法为 collection 记录嵌入配置（不影响使用）: {e}")
        return collection

    def _init_local_embed(self):
        """初始化本地嵌入模型（无需 API key），失败则降级为伪嵌入"""
        try:
            from sentence_transformers import SentenceTransformer
            self.local_model = SentenceTransformer('all-MiniLM-L6-v2')
            self.embed_model = "local"
            self.use_local_embed = True
            print("✅ 使用本地嵌入模型")
        except Exception as e:
            print(f"⚠️ 本地模型加载失败: {e}，将使用伪嵌入（全零向量）")
            self.embed_model = "dummy"
            self._dummy_embed = True
            self._dummy_embed_dim = 384   # 保持与常见模型一致的维度

    def _embed(self, text: str) -> list:
        if self.use_local_embed:
            return self.local_model.encode(text).tolist()
        elif self._dummy_embed:
            return [0.0] * self._dummy_embed_dim
        else:
            response = self.client.embeddings.create(
                model=self.embed_model,
                input=[text]
            )
            return response.data[0].embedding

    def _summarize(self, text: str, max_length: int = 100) -> str:
        # 本地模型或伪嵌入模式不支持摘要，直接截断
        if self.use_local_embed or self._dummy_embed:
            return text[:max_length]
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
        if summarize and not (self.use_local_embed or self._dummy_embed):
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

    # 以下方法保持不变
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
                # 把 id 并入 meta，供反馈闭环定位"这条回答用了哪几条记忆"，
                # 同时不改变返回元组长度，避免影响既有解包。
                meta = {**(meta or {}), "memory_id": mem_id}
                if meta.get("user_id") == user_id:
                    filtered.append((
                        results['documents'][0][i],
                        results['distances'][0][i],
                        meta
                    ))

        if not filtered and results['ids'] and results['ids'][0]:
            for i, mem_id in enumerate(results['ids'][0]):
                meta = results['metadatas'][0][i] if results['metadatas'] else {}
                meta = {**(meta or {}), "memory_id": mem_id}
                filtered.append((
                    results['documents'][0][i],
                    results['distances'][0][i],
                    meta
                ))

        return filtered[:top_k]

    def adjust_weights(self, memory_ids: list, delta: float) -> dict:
        """按反馈调整记忆权重，结果夹在 [0.1, 5.0]。

        权重直接影响检索排序（memory_router 按 relevance*weight 排序），
        所以被赞过的记忆更容易被召回、被踩的更难。
        """
        updated = []
        for mem_id in memory_ids or []:
            data = self.collection.get(ids=[mem_id])
            if not data.get("ids"):
                continue
            meta = (data.get("metadatas") or [{}])[0] or {}
            current = float(meta.get("weight", 1.0))
            meta["weight"] = round(max(0.1, min(5.0, current + delta)), 4)
            self.collection.update(ids=[mem_id], metadatas=[meta])
            updated.append({"id": mem_id, "weight": meta["weight"]})
        return {"status": "adjusted", "updated": updated}

    def delete_memory(self, memory_id: str) -> bool:
        try:
            self.collection.delete(ids=[memory_id])
            print(f"🗑️ 记忆已删除: {memory_id}")
            return True
        except Exception as e:
            print(f"❌ 删除记忆失败: {e}")
            return False

    def delete_memories_batch(self, memory_ids: list) -> dict:
        if not memory_ids:
            return {"status": "deleted", "count": 0}
        
        try:
            # 直接删除，ChromaDB 会忽略不存在的 ID
            # 注意：ChromaDB delete 不返回实际删除的数量，所以我们假设传入的有效 ID 都被删除
            # 为了避免 ChromaDB 内部 get 的 bug，我们不预先检查 ID 是否存在
            self.collection.delete(ids=memory_ids)
            
            # 由于无法从 delete 获取确切计数，我们返回传入的 ID 数量
            # 如果业务逻辑强依赖确切删除数，可能需要后续通过查询验证，但这会慢
            print(f"🗑️ 已执行批量删除操作，涉及 {len(memory_ids)} 个 ID")
            return {"status": "deleted", "count": len(memory_ids)}
            
        except Exception as e:
            print(f"❌ 批量删除记忆失败: {e}")
            traceback.print_exc()
            return {"error": str(e), "count": 0}

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