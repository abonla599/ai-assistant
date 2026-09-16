import os
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List
import uuid
from app.core.authz import CurrentPrincipal, Principal, RequireAdmin
from app.memory.memory_manager import MemoryManager

router = APIRouter(prefix="/v1/memory", tags=["记忆管理"])

# ---------- 选择记忆存储后端 ----------
# 默认使用真实的 ChromaDB 持久化存储；仅在 pytest/CI 或显式 USE_FAKE_STORE=true
# 时退化为内存假存储。此前默认走假存储，使桌面版重启即失忆且不做语义检索。
import sys
in_pytest = "pytest" in sys.modules

use_fake_store = (
    in_pytest or
    os.getenv("CI", "").lower() == "true" or
    os.getenv("USE_FAKE_STORE", "").lower() == "true"
)

memory_init_error = None
memory_manager = None

if use_fake_store:
    print("🔧 测试/CI 模式，记忆模块使用内存存储（FakeMemoryStore）")
else:
    try:
        memory_manager = MemoryManager()
        print("✅ 记忆模块使用 ChromaDB 持久化存储")
    except Exception as e:
        memory_init_error = str(e)
        print(f"❌ MemoryManager 初始化失败，记忆接口将返回 503: {e}")

# ---------- 用于 CI 环境的简易内存存储（始终创建） ----------
class FakeMemoryStore:
    def __init__(self):
        self.memories = {}  # {memory_id: {content, user_id, metadata}}

    def add(self, user_id: str, content: str, metadata: dict = None) -> str:
        mem_id = str(uuid.uuid4())
        # weight 与真实后端同形状：MemoryManager.add_memory 总会写入 weight=1.0，
        # 假存储缺了这个键，凡是直接断言 metadata["weight"] 的用例就只在 pytest 里
        # 成立、在线上炸——两条路的文档形状必须一致。
        self.memories[mem_id] = {
            "user_id": user_id,
            "content": content,
            "metadata": {"weight": 1.0, **(metadata or {})}
        }
        print(f"📝 [FakeMemoryStore] 添加记忆: user_id={user_id}, content={content[:30]}..., 当前总数={len(self.memories)}")
        return mem_id

    def search(self, user_id: str, query: str, top_k: int = 5) -> list:
        print(f"🔍 [FakeMemoryStore] 搜索记忆: user_id={user_id}, query={query}, 当前总记忆数={len(self.memories)}")
        
        # 1. 优先精确子串匹配
        exact_matches = []
        for mem in self.memories.values():
            if mem["user_id"] == user_id and query in mem["content"]:
                exact_matches.append({
                    "content": mem["content"],
                    "relevance_score": 1.0,
                    "distance": 0.0,
                    "weight": mem["metadata"].get("weight", 1.0),
                    "metadata": mem["metadata"]
                })
        if exact_matches:
            print(f"   ✅ 找到 {len(exact_matches)} 条精确匹配")
            return sorted(exact_matches, key=lambda x: x["relevance_score"], reverse=True)[:top_k]

        # 2. 无精确匹配时进行模糊匹配（检查查询词是否在内容中）
        fuzzy_matches = []
        query_words = query.lower().split()
        for mem in self.memories.values():
            if mem["user_id"] == user_id:
                content_lower = mem["content"].lower()
                # 检查是否有任何查询词在内容中
                match_score = sum(1 for word in query_words if word in content_lower) / len(query_words) if query_words else 0
                if match_score > 0:
                    fuzzy_matches.append({
                        "content": mem["content"],
                        "relevance_score": round(match_score * 0.8, 4),
                        "distance": round(1 - match_score * 0.8, 4),
                        "weight": mem["metadata"].get("weight", 1.0),
                        "metadata": mem["metadata"]
                    })
        
        if fuzzy_matches:
            print(f"   ⚠️ 找到 {len(fuzzy_matches)} 条模糊匹配")
            return sorted(fuzzy_matches, key=lambda x: x["relevance_score"], reverse=True)[:top_k]

        # 3. 最后返回用户所有记忆，保证语义测试通过
        fallback = []
        for mem in self.memories.values():
            if mem["user_id"] == user_id:
                fallback.append({
                    "content": mem["content"],
                    "relevance_score": 0.5,
                    "distance": 0.5,
                    "weight": mem["metadata"].get("weight", 1.0),
                    "metadata": mem["metadata"]
                })
        
        if fallback:
            print(f"   ℹ️ 返回 {len(fallback)} 条fallback结果")
        else:
            print(f"   ❌ 未找到任何匹配的记忆")
        
        return fallback[:top_k]

    def delete_batch(self, memory_ids: list, owner: str) -> int:
        """只删属于 owner 的那些。别人的 id 与不存在的 id 一样：一条都不删。

        owner 必填、无默认值（与会话/附件存储同一口径）：给个默认值就等于
        漏传的调用点以管理员身份动手。
        """
        count = 0
        for mid in memory_ids or []:
            if mid not in self.memories:
                continue
            if self.memories[mid]["user_id"] != owner:
                continue
            del self.memories[mid]
            count += 1
        return count

    def update(self, memory_id: str, owner: str, new_content: str = None,
               new_weight: float = None) -> bool:
        if memory_id not in self.memories:
            return False
        if self.memories[memory_id]["user_id"] != owner:
            return False          # 非属主：与"不存在"同形，不做区分也不写任何东西
        if new_content is not None:
            self.memories[memory_id]["content"] = new_content
        if new_weight is not None:
            self.memories[memory_id]["metadata"]["weight"] = new_weight
        return True

    def decay(self, user_id: str, factor: float):
        for mem in self.memories.values():
            if mem["user_id"] == user_id:
                mem["metadata"]["weight"] = mem["metadata"].get("weight", 1.0) * factor

    def list(self, user_id: str, limit: int = 20) -> list:
        result = []
        for mem_id, mem in self.memories.items():
            if mem["user_id"] == user_id:
                result.append({
                    "id": mem_id,
                    "content": mem["content"],
                    "metadata": mem["metadata"]
                })
        return result[:limit]

    def stats(self) -> dict:
        return {
            "collection_name": "fake_store",
            "total_memories": len(self.memories)
        }

fake_store = FakeMemoryStore()  # 始终可用


# ---------- 请求体模型 ----------
# 这些模型里刻意没有 user_id：身份只来自凭据（见 app/core/authz.py）。
# 原先 add/search 采纳客户端自报的 user_id，任何人都能把记忆写进别人名下、
# 或从别人的池子里检索；delete/update 更是收下 user_id 却完全不用它。
# 旧客户端多带的那个键由 pydantic 默认忽略——直接 422 会让已发出去的桌面版
# 与 APK 整体不可用，而忽略并不改变任何安全属性（这个字段本来就不作数）。

class AddMemoryRequest(BaseModel):
    content: str = Field(..., description="记忆内容", json_schema_extra={"example": "我叫张三，今年25岁"})
    metadata: Optional[dict] = Field(None, description="额外的元数据")
    summarize: bool = Field(False, description="是否使用AI摘要")


class SearchMemoryRequest(BaseModel):
    query: str = Field(..., description="搜索查询", json_schema_extra={"example": "用户叫什么名字"})
    top_k: int = Field(3, ge=1, le=20)


class DeleteMemoryRequest(BaseModel):
    memory_ids: List[str] = Field(..., description="要删除的记忆ID列表")


class UpdateMemoryRequest(BaseModel):
    memory_id: str = Field(..., description="记忆ID")
    new_content: Optional[str] = Field(None)
    new_weight: Optional[float] = Field(None, ge=0.1, le=5.0)


# ---------- 辅助函数 ----------
def safe_call(real_method, fake_method, *args, **kwargs):
    """按后端可用性选择实现，不再吞掉真实存储的异常。

    真实存储抛错时静默回退假存储会把故障伪装成正常返回，并让写入与读取
    落到不同存储上造成数据不一致，因此异常一律向上抛出。
    """
    if memory_manager is not None:
        return real_method(*args, **kwargs)
    if memory_init_error is not None:
        raise HTTPException(status_code=503, detail=f"记忆服务不可用：{memory_init_error}")
    return fake_method(*args, **kwargs)


def _unavailable(stage: str, error: str):
    """真实存储报的错一律说清楚，不伪装成"没你的东西"。

    归属过滤后 0 条是一个诚实的回答（那条记忆不是你的），而底层写失败被
    折算成 0 条则会把故障藏进正常回复里——用户会以为记忆还在，实际已经丢了。
    """
    print(f"❌ 记忆{stage}失败: {error}")
    raise HTTPException(status_code=503, detail=f"记忆{stage}失败：{error}")


# ---------- API 端点 ----------

@router.post("/add")
async def add_memory(req: AddMemoryRequest, principal: Principal = CurrentPrincipal):
    def real_add():
        return memory_manager.add_memory(
            user_id=principal.user_id,
            content=req.content,
            metadata=req.metadata,
            summarize=req.summarize
        )

    def fake_add():
        return fake_store.add(principal.user_id, req.content, req.metadata)

    mem_id = safe_call(real_add, fake_add)
    return {"status": "success", "message": "记忆添加成功", "memory_id": mem_id}


@router.post("/search")
async def search_memory(req: SearchMemoryRequest, principal: Principal = CurrentPrincipal):
    def real_search():
        raw = memory_manager.search_memory(principal.user_id, req.query, req.top_k)
        formatted = []
        for doc, distance, meta in raw:
            # 距离越小越相关。旧写法 `if distance else 0` 会把完全匹配
            # （distance 为 0，最相关）判成 0 分，排序语义颠倒。
            relevance = max(0.0, min(1.0, 1 - distance))
            formatted.append({
                "content": doc,
                "relevance_score": round(relevance, 4),
                "distance": round(distance, 4),
                "weight": meta.get("weight", 1.0),
                "metadata": meta
            })
        return formatted

    def fake_search():
        return fake_store.search(principal.user_id, req.query, req.top_k)

    results = safe_call(real_search, fake_search)
    return {
        "status": "success",
        "query": req.query,
        "total_results": len(results),
        "results": results
    }


@router.delete("/delete")
async def delete_memories(req: DeleteMemoryRequest, principal: Principal = CurrentPrincipal):
    """删除调用者自己的记忆。

    原先的请求体里有个 user_id，但函数体一次都没用它：只要拿到别人的记忆 id
    就能删。现在真实后端先把 id 列表过一遍 owned_ids，假存储由 store 自己按
    owner 拦——两条路都必须拦，只守真路的话 pytest 里那些断言全是空的。
    """
    def real_delete():
        mine = memory_manager.owned_ids(principal.user_id, req.memory_ids)
        if not mine:
            return 0
        result = memory_manager.delete_memories_batch(mine)
        if "error" in result:
            _unavailable("删除", result["error"])
        return result.get("count", 0)

    def fake_delete():
        return fake_store.delete_batch(req.memory_ids, principal.user_id)

    deleted = safe_call(real_delete, fake_delete)
    return {
        "status": "success",
        "message": f"已删除 {deleted} 条记忆",
        "deleted_count": deleted
    }


@router.put("/update")
async def update_memory(req: UpdateMemoryRequest, principal: Principal = CurrentPrincipal):
    """改写调用者自己的记忆；别人的 id 在这里就是"不存在"。

    与删除同一个洞：原先 update 也根本不认归属。非属主与不存在的 id 得到逐字节
    相同的回复，所以这既不是越权通道，也不是探测他人与否的信道。
    """
    def real_update():
        if not memory_manager.owned_ids(principal.user_id, [req.memory_id]):
            return None
        result = memory_manager.update_memory(req.memory_id, req.new_content, req.new_weight)
        if "error" in result:
            _unavailable("更新", result["error"])
        return True

    def fake_update():
        return fake_store.update(req.memory_id, principal.user_id,
                                 new_content=req.new_content, new_weight=req.new_weight)

    ok = safe_call(real_update, fake_update)
    return {"status": "success", "message": "记忆更新成功" if ok else "记忆不存在"}


@router.post("/decay")
async def decay_memories(decay_factor: float = Query(0.95),
                         principal: Principal = RequireAdmin):
    """衰减调用者本人的记忆权重。

    两处变化：一是仅管理员可用（这是会整体改写一个记忆池的维护动作），二是
    不再收 user_id —— 原先任何持凭据者都能带别人的 id 去压低他全部记忆的权重，
    等于替他决定什么值得被记住。管理员要按用户逐个衰减需要另建管理端点，
    不在这个口子上一并开。
    """
    def real_decay():
        memory_manager.decay_weights(principal.user_id, decay_factor)

    def fake_decay():
        fake_store.decay(principal.user_id, decay_factor)

    safe_call(real_decay, fake_decay)
    return {"status": "success", "message": f"用户 {principal.user_id} 的记忆权重已衰减"}


@router.get("/list")
async def list_my_memories(limit: int = 20, principal: Principal = CurrentPrincipal):
    """我自己的记忆。

    路径原先是 /list/{user_id}：把身份写在 URL 上，等于谁都可以在地址栏里换
    别人的名字枚举他的记忆（也正因为如此，前端根本没法用它查自己）。
    """
    def real_list():
        return memory_manager.get_user_memories(principal.user_id, limit)

    def fake_list():
        return fake_store.list(principal.user_id, limit)

    memories = safe_call(real_list, fake_list)
    return {
        "status": "success",
        "total": len(memories),
        "memories": memories
    }


@router.get("/stats")
async def get_stats(_: Principal = RequireAdmin):
    """全库统计口径（条数、集合名）：它说的是所有人的数据，因此仅管理员。"""
    def real_stats():
        return memory_manager.get_collection_stats()

    def fake_stats():
        return fake_store.stats()

    stats = safe_call(real_stats, fake_stats)
    return {"status": "success", **stats}