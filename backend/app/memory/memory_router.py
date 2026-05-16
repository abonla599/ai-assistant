from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
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


class SearchMemoryRequest(BaseModel):
    """搜索记忆请求"""
    user_id: str = Field(..., description="用户ID", example="user_001")
    query: str = Field(..., description="搜索查询", example="用户叫什么名字")
    top_k: int = Field(default=3, description="返回结果数量", ge=1, le=20)


class DeleteMemoryRequest(BaseModel):
    """删除记忆请求"""
    memory_id: str = Field(..., description="记忆ID")


# ============ API 端点 ============

@router.post("/add")
async def add_memory(req: AddMemoryRequest):
    """
    添加一条用户记忆
    
    - **user_id**: 用户唯一标识
    - **content**: 要存储的记忆内容
    - **metadata**: 可选的额外信息（如情感标签、类别等）
    """
    try:
        mem_id = memory_manager.add_memory(
            user_id=req.user_id,
            content=req.content,
            metadata=req.metadata
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
    
    - **user_id**: 用户唯一标识
    - **query**: 搜索查询文本
    - **top_k**: 返回的最相关记忆数量（1-20）
    """
    try:
        results = memory_manager.search_memory(
            user_id=req.user_id,
            query=req.query,
            top_k=req.top_k
        )
        
        # 格式化返回结果
        formatted = []
        for doc, distance, meta in results:
            formatted.append({
                "content": doc,
                "relevance_score": round(1 - distance, 4) if distance else 0,
                "distance": round(distance, 4) if distance else 0,
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


@router.post("/delete")
async def delete_memory(req: DeleteMemoryRequest):
    """
    删除一条记忆
    
    - **memory_id**: 要删除的记忆ID
    """
    try:
        success = memory_manager.delete_memory(req.memory_id)
        if success:
            return {
                "status": "success",
                "message": f"记忆 {req.memory_id} 已删除"
            }
        else:
            raise HTTPException(status_code=404, detail="记忆不存在或删除失败")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除记忆失败: {str(e)}")


@router.get("/list/{user_id}")
async def list_user_memories(user_id: str, limit: int = 20):
    """
    获取用户的所有记忆列表
    
    - **user_id**: 用户唯一标识
    - **limit**: 返回数量限制（默认20）
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