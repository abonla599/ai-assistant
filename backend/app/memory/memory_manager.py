import os
import uuid
import traceback
import chromadb
import httpx
from openai import OpenAI

from app.core.paths import data_root, load_project_env
from app.core.tls import system_ssl_context
from app.memory.ranking import weighted_rank

load_project_env()

# 禁用 chromadb 遥测，避免 CI 中报错干扰
os.environ["ANONYMIZED_TELEMETRY"] = "False"


def _default_persist_dir() -> str:
    """记忆库位置的唯一事实来源，避免依赖进程工作目录。

    优先级：CHROMA_DB_PATH 环境变量 > 项目根目录。
    打包版原先落在 EXE 同级目录，而 dist/run_backend 每次重建都会被整体删除，
    等于一次构建抹光长期记忆；改为项目根后桌面版与源码版共用同一份记忆库。
    """
    env_path = os.getenv("CHROMA_DB_PATH")
    if env_path:
        return os.path.abspath(env_path)
    return os.path.join(data_root(), "chroma_db")


class MemoryManager:
    """记忆管理器：负责存储和检索用户记忆"""

    def __init__(self, collection_name="user_memories", persist_dir=None, embedding_fn=None):
        self.persist_dir = os.path.abspath(persist_dir or _default_persist_dir())
        try:
            self.use_local_embed = False
            self._dummy_embed = False   # 伪嵌入标记
            self._injected_embed = embedding_fn

            if embedding_fn is not None:
                # 测试注入确定性向量：既不打付费嵌入接口，也不依赖本机是否装得下
                # 本地模型。借用 _dummy_embed 标记走"不做 LLM 摘要"那条既有分支。
                self.embed_model = "injected"
                self._dummy_embed = True
            else:
                api_key = os.getenv("api_key")

                if api_key:
                    try:
                        self.client = OpenAI(
                            api_key=api_key,
                            base_url="https://api.apiyi.com/v1",
                            # 嵌入接口一旦因信任锚不对而握手失败，下面会静默降级成
                            # 伪嵌入：不报错，但语义检索再也读不到记忆。所以这里必须
                            # 与聊天用同一个信任锚，见 app/core/tls.py
                            http_client=httpx.Client(verify=system_ssl_context())
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

            if embedding_fn is not None:
                mode = "注入(仅测试)"
            elif self.use_local_embed:
                mode = "本地"
            elif self._dummy_embed:
                mode = "伪嵌入"
            else:
                mode = "云端"
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
        if self._injected_embed is not None:
            return list(self._injected_embed(text))
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

        # 顺序就是安全属性：spread 在前、归属在后。
        # 写成 {"user_id": user_id, ..., **(metadata or {})} 时，客户端只要发
        # {"content": "...", "metadata": {"user_id": "别人"}} 就把这条记忆挂到那个
        # 人名下——文本随后被 search_memory 召回并注入他的系统提示，等于跨用户投毒。
        # 归属一律由服务端从凭据推导，metadata 里带来的同名键覆盖不动它。
        meta = {
            "weight": 1.0,
            **(metadata or {}),
            "user_id": user_id,
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
        """只返回该用户自己的记忆。

        过滤必须下推给 chroma：旧写法先全局取 top_k*2 再在 Python 里按 user_id
        挑，别人的记忆会把本人的挤出这个窗口；更糟的是窗口内一条都不属于本人时，
        兜底逻辑会把别人的记忆原样返回，构成跨用户泄露。
        """
        total = self.collection.count()
        if not total:
            return []

        # 多取一些再按权重重排：只问 top_k 条的话，权重永远救不回被距离排在窗口外的
        # 记忆——那等于反馈白给。截断在重排之后做，注入上下文的条数不会失控。
        fetch = max(top_k * 3, top_k + 5)
        results = self.collection.query(
            query_embeddings=[self._embed(query)],
            n_results=max(1, min(fetch, total)),
            where={"user_id": user_id},
        )

        ids = (results.get("ids") or [[]])[0]
        docs = (results.get("documents") or [[]])[0]
        dists = (results.get("distances") or [[]])[0]
        metas = (results.get("metadatas") or [[]])[0]

        entries = []
        for i, mem_id in enumerate(ids):
            # 把 id 并入 meta，供反馈闭环定位"这条回答用了哪几条记忆"，
            # 同时不改变返回元组长度，避免影响既有解包。
            meta = {**(metas[i] or {}), "memory_id": mem_id}
            entries.append((1.0 - dists[i], meta.get("weight", 1.0),
                            (docs[i], dists[i], meta)))
        return weighted_rank(entries, top_k)

    def adjust_weights(self, memory_ids: list, owner: str, delta: float) -> dict:
        """按反馈调整记忆权重，结果夹在 [0.1, 5.0]。

        权重直接影响检索排序：真存储与内存替身两条路都走
        `app.memory.ranking.weighted_rank`（relevance * weight），所以被赞过的记忆
        更容易被召回、被踩的更难。这句话在 2026-09-19 之前是假的——两处 sort 键
        都只有 relevance_score。

        owner 必填、无默认值，且筛选写在本方法内部：整份回写会话的端点接受客户端
        自填的 memory_ids，"消息是你的"并不等于"那些记忆是你的"。闸门如果只放在
        调用方手里（先 owned_ids 筛一遍再传裸 id），未来任何一个漏筛的调用点都
        类型正确、照样替别人改权重。
        """
        updated = []
        for mem_id in self.owned_ids(owner, memory_ids):
            data = self.collection.get(ids=[mem_id])
            if not data.get("ids"):
                continue
            meta = (data.get("metadatas") or [{}])[0] or {}
            current = float(meta.get("weight", 1.0))
            meta["weight"] = round(max(0.1, min(5.0, current + delta)), 4)
            # 归属由存储自己钉住：owned_ids 已确认这个人拥有这条记忆，回写时
            # 不能被 metadata 里残留的旧值改回去。
            meta["user_id"] = owner
            self.collection.update(ids=[mem_id], metadatas=[meta])
            updated.append({"id": mem_id, "weight": meta["weight"]})
        return {"status": "adjusted", "updated": updated}

    def owned_ids(self, user_id: str, ids: list) -> list:
        """只保留确实属于该用户的记忆 id——本模块内部助手，不是唯一的闸门。

        删除/改权重原先直接拿客户端给的 id 就动手，等于任何人可改任何人的记忆。
        归属判定交给 chroma 的 where 条件，而不是取回来再在 Python 里挑：
        与 search_memory 同一套下推口径，也就不会因窗口或分页漏判。
        非属主与不存在的 id 得到同一个结果（都不在返回列表里），调用方因此
        不是一条探测他人记忆的信道。

        现在 delete_memories_batch / update_memory / adjust_weights 各自在方法内部
        调它：调用点无需"记得先筛一遍"，也不可能忘。留着公开是因为它同时是
        "按人挑出 id" 这一读侧需求的唯一实现（测试与将来的运维端点复用）。
        """
        wanted = {str(i) for i in ids or []}
        if not wanted:
            return []
        got = self.collection.get(ids=list(wanted), where={"user_id": user_id})
        return list(got.get("ids") or [])

    def delete_memories_batch(self, memory_ids: list, owner: str) -> dict:
        """批量删除，且只删确实属于 owner 的那些。

        owner 必填、无默认值，筛选写在方法内部（与会话/附件存储同一口径）：
        收裸 id 列表又靠调用方自觉预筛，下一个调用点忘了就是任何人可删任何人的
        记忆，而类型检查一声不响。非属主与不存在的 id 同样被静默丢弃——本方法
        不区分两者，调用方也就无法被用来探测某个 id 是否存在。
        """
        mine = self.owned_ids(owner, memory_ids)
        if not mine:
            return {"status": "deleted", "count": 0}

        try:
            # 直接删除，ChromaDB 会忽略不存在的 ID
            # 注意：ChromaDB delete 不返回实际删除的数量，所以我们假设传入的有效 ID 都被删除
            # 为了避免 ChromaDB 内部 get 的 bug，我们不预先检查 ID 是否存在
            self.collection.delete(ids=mine)

            # 由于无法从 delete 获取确切计数，我们返回传入的 ID 数量
            # 如果业务逻辑强依赖确切删除数，可能需要后续通过查询验证，但这会慢
            print(f"🗑️ 已执行批量删除操作，涉及 {len(mine)} 个 ID")
            return {"status": "deleted", "count": len(mine)}

        except Exception as e:
            print(f"❌ 批量删除记忆失败: {e}")
            traceback.print_exc()
            return {"error": str(e), "count": 0}

    def update_memory(self, memory_id: str, owner: str, new_content: str = None,
                      new_weight: float = None) -> dict:
        """改写一条记忆，owner 必填——不是自己的那条就当不存在。

        返回 status=not_found 而不是 error：前者是"没有你的这条记忆"这个正常
        答案，后者是底层故障，路由对两者的处理完全不同（故障会报 503，
        not_found 仍是 200）。把非属主混进 error 里，就是拿故障码替别人确认 id 存在。
        """
        if not self.owned_ids(owner, [memory_id]):
            return {"status": "not_found"}

        data = self.collection.get(ids=[memory_id])
        if not data['ids']:
            return {"status": "not_found"}

        doc = new_content if new_content else data['documents'][0]
        meta = data['metadatas'][0] or {}
        if new_weight is not None:
            meta['weight'] = new_weight
        # 归属由存储自己钉住：owned_ids 已经确认这个人拥有这条记忆，
        # 回写时不能被 metadata 里残留的旧值改回别人名下。
        meta['user_id'] = owner

        # 底层写失败要报出来（路由据此给 503），不能像原先那样和"没有这条记忆"
        # 共用一个答案——那会把故障伪装成一个正常回复。
        try:
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
        except Exception as e:
            print(f"❌ 更新记忆失败: {e}")
            traceback.print_exc()
            return {"error": str(e)}
        return {"status": "updated"}

    def decay_weights(self, user_id: str, decay_factor: float = 0.95):
        # 归属下推给 chroma，与 search_memory/owned_ids/get_user_memories 同一套口径：
        # 原先这里 get() 拉整库再在 Python 里挑，等于把"别人的记忆也搬进本进程"，
        # 条数与体积都不封顶（本文件 :389 的 docstring 早就写了该下推）。
        all_data = self.collection.get(where={"user_id": user_id})
        ids_to_update = []
        new_metadatas = []

        for i, meta in enumerate(all_data['metadatas']):
            new_weight = (meta or {}).get('weight', 1.0) * decay_factor
            meta = dict(meta or {})
            meta['weight'] = new_weight
            ids_to_update.append(all_data['ids'][i])
            new_metadatas.append(meta)

        if ids_to_update:
            self.collection.update(ids=ids_to_update, metadatas=new_metadatas)
            print(f"🧠 已衰减 {len(ids_to_update)} 条记忆的权重")
        else:
            print(f"⚠️ 未找到用户 {user_id} 的记忆")

    def get_user_memories(self, user_id: str, limit: int = 20) -> list:
        """某个用户的记忆列表。

        这里原先包着一层 `except Exception: return []`：底层一故障，
        GET /v1/memory/list 就回 200 + 空列表，用户以为自己的记忆全没了，
        运维则看到一个"一切正常"的接口。故障必须抛出来说明白，删除与更新已经
        这么改了（路由侧统一转 503），读取不能留最后一条把故障藏进正常回复的路。

        归属判定下推给 chroma（`where=`），与 search_memory、owned_ids 同一套口径：
        旧写法是 `collection.get()` 取回**整个库**再在 Python 里逐条挑，于是这个人
        翻一次列表的代价随所有人的记忆总量增长，`limit` 只截得了返回给它的条数、
        截不了已经搬进内存的那份。下推之后条数与体积都由 where + limit 一起封顶。
        """
        data = self.collection.get(where={"user_id": user_id}, limit=max(0, int(limit)))
        ids = data.get("ids") or []
        documents = data.get("documents") or []
        metadatas = data.get("metadatas") or []
        return [{
            "id": mem_id,
            "content": documents[i] if i < len(documents) else "",
            "metadata": metadatas[i] if i < len(metadatas) else {},
        } for i, mem_id in enumerate(ids)]

    def get_collection_stats(self) -> dict:
        count = self.collection.count()
        return {
            "collection_name": self.collection.name,
            "total_memories": count
        }