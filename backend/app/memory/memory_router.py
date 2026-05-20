import os
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List
import uuid
from app.memory.memory_manager import MemoryManager

router = APIRouter(prefix="/v1/memory", tags=["记忆管理"])

# ---------- 根据环境决定是否使用真实 MemoryManager ----------
force_fake = os.getenv("CI", "").lower() == "true" or os.getenv("USE_FAKE_STORE", "").lower() == "true"

if force_fake:
    print("🔧 CI/强制模拟存储模式，使用内存存储")
    memory_manager = None
else:
    try:
        memory_manager = MemoryManager()
    except Exception as e:
        memory_manager = None
        print(f"❌ MemoryManager 初始化失败，使用模拟存储: {e}")

# ---------- 用于 CI 环境的简易内存存储（始终创建） ----------
class FakeMemoryStore:
    def __init__(self):
        self.memories = {}  # {memory_id: {content, user_id, metadata}}

    def add(self, user_id: str, content: str, metadata: dict = None) -> str:
        mem_id = str(uuid.uuid4())
        self.memories[mem_id] = {
            "user_id": user_id,
            "content": content,
            "metadata": metadata or {}
        }
        return mem_id

    def search(self, user_id: str, query: str, top_k: int = 5) -> list:
        results = []
        for mem in self.memories.values():
            if mem["user_id"] == user_id and query in mem["content"]:
                results.append({
                    "content": mem["content"],
                    "relevance_score": 1.0,
                    "distance": 0.0,
                    "weight": mem["metadata"].get("weight", 1.0),
                    "metadata": mem["metadata"]
                })
        return results[:top_k]

    def delete_batch(self, memory_ids: list) -> int:
        count = 0
        for mid in memory_ids:
            if mid in self.memories:
                del self.memories[mid]
                count += 1
        return count

    def update(self, memory_id: str, new_content: str = None, new_weight: float = None) -> bool:
        if memory_id not in self.memories:
            return False
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
class AddMemoryRequest(BaseModel):
    user_id: str = Field(..., description="用户ID", json_schema_extra={"example": "user_001"})
    content: str = Field(..., description="记忆内容", json_schema_extra={"example": "我叫张三，今年25岁"})
    metadata: Optional[dict] = Field(None, description="额外的元数据")
    summarize: bool = Field(False, description="是否使用AI摘要")


class SearchMemoryRequest(BaseModel):
    user_id: str = Field(..., description="用户ID", json_schema_extra={"example": "user_001"})
    query: str = Field(..., description="搜索查询", json_schema_extra={"example": "用户叫什么名字"})
    top_k: int = Field(3, ge=1, le=20)


class DeleteMemoryRequest(BaseModel):
    user_id: str = Field(..., description="用户ID")
    memory_ids: List[str] = Field(..., description="要删除的记忆ID列表")


class UpdateMemoryRequest(BaseModel):
    memory_id: str = Field(..., description="记忆ID")
    new_content: Optional[str] = Field(None)
    new_weight: Optional[float] = Field(None, ge=0.1, le=5.0)


# ---------- 辅助函数 ----------
def safe_call(real_method, fake_method, *args, **kwargs):
    """如果 memory_manager 可用且不抛异常则调用，否则调用 fake_method"""
    if memory_manager is not None:
        try:
            return real_method(*args, **kwargs)
        except Exception:
            pass  # 回退到 fake_store
    return fake_method(*args, **kwargs)


# ---------- API 端点 ----------

@router.post("/add")
async def add_memory(req: AddMemoryRequest):
    def real_add():
        return memory_manager.add_memory(
            user_id=req.user_id,
            content=req.content,
            metadata=req.metadata,
            summarize=req.summarize
        )

    def fake_add():
        return fake_store.add(req.user_id, req.content, req.metadata)

    mem_id = safe_call(real_add, fake_add)
    return {"status": "success", "message": "记忆添加成功", "memory_id": mem_id}


@router.post("/search")
async def search_memory(req: SearchMemoryRequest):
    def real_search():
        raw = memory_manager.search_memory(req.user_id, req.query, req.top_k)
        formatted = []
        for doc, distance, meta in raw:
            formatted.append({
                "content": doc,
                "relevance_score": round(1 - distance, 4) if distance else 0,
                "distance": round(distance, 4) if distance else 0,
                "weight": meta.get("weight", 1.0),
                "metadata": meta
            })
        return formatted

    def fake_search():
        return fake_store.search(req.user_id, req.query, req.top_k)

    results = safe_call(real_search, fake_search)
    return {
        "status": "success",
        "query": req.query,
        "total_results": len(results),
        "results": results
    }


@router.delete("/delete")
async def delete_memories(req: DeleteMemoryRequest):
    def real_delete():
        result = memory_manager.delete_memories_batch(req.memory_ids)
        if "error" in result:
            return 0
        return result.get("count", 0)

    def fake_delete():
        return fake_store.delete_batch(req.memory_ids)

    deleted = safe_call(real_delete, fake_delete)
    return {
        "status": "success",
        "message": f"已删除 {deleted} 条记忆",
        "deleted_count": deleted
    }


@router.put("/update")
async def update_memory(req: UpdateMemoryRequest):
    def real_update():
        result = memory_manager.update_memory(req.memory_id, req.new_content, req.new_weight)
        if "error" in result:
            return False
        return True

    def fake_update():
        return fake_store.update(req.memory_id, req.new_content, req.new_weight)

    ok = safe_call(real_update, fake_update)
    return {"status": "success", "message": "记忆更新成功" if ok else "记忆不存在"}


@router.post("/decay")
async def decay_memories(
    user_id: str = Query(...),
    decay_factor: float = Query(0.95)
):
    def real_decay():
        memory_manager.decay_weights(user_id, decay_factor)

    def fake_decay():
        fake_store.decay(user_id, decay_factor)

    safe_call(real_decay, fake_decay)
    return {"status": "success", "message": f"用户 {user_id} 的记忆权重已衰减"}


@router.get("/list/{user_id}")
async def list_user_memories(user_id: str, limit: int = 20):
    def real_list():
        return memory_manager.get_user_memories(user_id, limit)

    def fake_list():
        return fake_store.list(user_id, limit)

    memories = safe_call(real_list, fake_list)
    return {
        "status": "success",
        "user_id": user_id,
        "total": len(memories),
        "memories": memories
    }


@router.get("/stats")
async def get_stats():
    def real_stats():
        return memory_manager.get_collection_stats()

    def fake_stats():
        return fake_store.stats()

    stats = safe_call(real_stats, fake_stats)
    return {"status": "success", **stats}