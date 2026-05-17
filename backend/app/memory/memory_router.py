from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List
from app.memory.memory_manager import MemoryManager

# 创建路由器
router = APIRouter(prefix="/v1/memory", tags=["记忆管理"])

# 实例化记忆管理器（单例）
memory_manager = MemoryManager()


# ============ 请求体模型 ============

class AddMemoryRequest(BaseModel):
    """添加记忆请求"""
    user_id: str = Field(..., description="用户ID", example="user_001")
    content: str = Field(..., description="记忆内容", example="我叫张三，今年25岁")
    metadata: Optional[dict] = Field(
        default=None,
        description="额外的元数据",
        example={"emotion": "neutral", "category": "personal_info"}
    )
    summarize: bool = Field(
        default=False,
        description="是否使用AI将长文本压缩为一句话（默认False则自动截断前200字符）"
    )


class SearchMemoryRequest(BaseModel):
    """搜索记忆请求"""
    user_id: str = Field(..., description="用户ID", example="user_001")
    query: str = Field(..., description="搜索查询", example="用户叫什么名字")
    top_k: int = Field(default=3, description="返回结果数量", ge=1, le=20)


class DeleteMemoryRequest(BaseModel):
    """批量删除记忆请求"""
    user_id: str = Field(..., description="用户ID，用于记录（暂不做权限校验）")
    memory_ids: List[str] = Field(..., description="要删除的记忆ID列表")


class UpdateMemoryRequest(BaseModel):
    """更新记忆请求"""
    memory_id: str = Field(..., description="记忆ID")
    new_content: Optional[str] = Field(None, description="新的记忆内容")
    new_weight: Optional[float] = Field(None, description="新的权重值", ge=0.1, le=5.0)


# ============ API 端点 ============

@router.post("/add")
async def add_memory(req: AddMemoryRequest):
    """
    添加一条用户记忆（支持自动截断或AI摘要）
    
    - **summarize**: 设为 true 会调用 AI 将长文本压缩成一句话
    """
    try:
        mem_id = memory_manager.add_memory(
            user_id=req.user_id,
            content=req.content,
            metadata=req.metadata,
            summarize=req.summarize
        )
        return {
            "status": "success",
            "message": "记忆添加成功",
            "memory_id": mem_id
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"添加记忆失败: {str(e)}")


@router.post("/search")
async def search_memory(req: SearchMemoryRequest):
    """
    语义搜索用户记忆
    """
    try:
        results = memory_manager.search_memory(
            user_id=req.user_id,
            query=req.query,
            top_k=req.top_k
        )

        formatted = []
        for doc, distance, meta in results:
            formatted.append({
                "content": doc,
                "relevance_score": round(1 - distance, 4) if distance else 0,
                "distance": round(distance, 4) if distance else 0,
                "weight": meta.get("weight", 1.0),
                "metadata": meta
            })

        return {
            "status": "success",
            "query": req.query,
            "total_results": len(formatted),
            "results": formatted
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"搜索记忆失败: {str(e)}")


@router.delete("/delete")
async def delete_memories(req: DeleteMemoryRequest):
    """
    批量删除记忆（传入 memory_ids 列表）
    """
    try:
        result = memory_manager.delete_memories_batch(req.memory_ids)
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return {
            "status": "success",
            "message": f"已删除 {result['count']} 条记忆",
            "deleted_count": result["count"]
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"批量删除失败: {str(e)}")


@router.put("/update")
async def update_memory(req: UpdateMemoryRequest):
    """
    更新记忆的内容或权重
    """
    try:
        result = memory_manager.update_memory(req.memory_id, req.new_content, req.new_weight)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return {"status": "success", "message": "记忆更新成功"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新记忆失败: {str(e)}")


@router.post("/decay")
async def decay_memories(
    user_id: str = Query(..., description="用户ID"),
    decay_factor: float = Query(0.95, description="衰减因子（0~1之间）")
):
    """
    衰减指定用户所有记忆的权重（模拟遗忘曲线）
    """
    try:
        memory_manager.decay_weights(user_id, decay_factor)
        return {"status": "success", "message": f"用户 {user_id} 的记忆权重已衰减"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"衰减失败: {str(e)}")


@router.get("/list/{user_id}")
async def list_user_memories(user_id: str, limit: int = 20):
    """
    获取用户的所有记忆列表
    """
    try:
        memories = memory_manager.get_user_memories(user_id, limit)
        return {
            "status": "success",
            "user_id": user_id,
            "total": len(memories),
            "memories": memories
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取记忆列表失败: {str(e)}")


@router.get("/stats")
async def get_stats():
    """获取记忆库统计信息"""
    try:
        stats = memory_manager.get_collection_stats()
        return {"status": "success", **stats}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取统计失败: {str(e)}")