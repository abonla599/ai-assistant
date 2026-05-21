#!/usr/bin/env python3
"""Quick test runner to verify fixes"""
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_root():
    """Test root endpoint"""
    res = client.get("/")
    assert res.status_code == 200
    data = res.json()
    assert "status" in data
    assert data["status"] == "running"
    print("✅ test_root PASSED")

def test_memory_update():
    """Test memory update functionality"""
    # Add memory
    add_res = client.post("/v1/memory/add", json={
        "user_id": "test_upd",
        "content": "old content",
        "summarize": False
    })
    assert add_res.status_code == 200
    mem_id = add_res.json()["memory_id"]

    # Update memory
    update_res = client.put("/v1/memory/update", json={
        "memory_id": mem_id,
        "new_content": "new content"
    })
    assert update_res.status_code == 200

    # Search for updated content
    search_res = client.post("/v1/memory/search", json={
        "user_id": "test_upd",
        "query": "new content",
        "top_k": 1
    })
    results = search_res.json()["results"]
    assert len(results) > 0
    assert "new content" in results[0]["content"]
    print("✅ test_memory_update PASSED")

def test_list_user_memories():
    """Test list user memories"""
    # Add memories
    client.post("/v1/memory/add", json={
        "user_id": "list_test",
        "content": "memory 1",
        "summarize": False
    })
    client.post("/v1/memory/add", json={
        "user_id": "list_test",
        "content": "memory 2",
        "summarize": False
    })

    # List memories
    list_res = client.get("/v1/memory/list/list_test?limit=10")
    assert list_res.status_code == 200
    data = list_res.json()
    assert data["total"] >= 2
    print(f"✅ test_list_user_memories PASSED (found {data['total']} memories)")

def test_decay():
    """Test memory decay"""
    # Add memories
    client.post("/v1/memory/add", json={
        "user_id": "decay_test",
        "content": "memory X",
        "summarize": False
    })

    # Decay
    decay_res = client.post("/v1/memory/decay?user_id=decay_test&decay_factor=0.5")
    assert decay_res.status_code == 200
    assert decay_res.json()["status"] == "success"

    # Search and check weight
    search_res = client.post("/v1/memory/search", json={
        "user_id": "decay_test",
        "query": "memory",
        "top_k": 5
    })
    results = search_res.json()["results"]
    assert len(results) > 0
    weights = [r["weight"] for r in results]
    assert all(w < 1.0 for w in weights), f"Weights should be < 1.0, got: {weights}"
    print("✅ test_decay PASSED")

if __name__ == "__main__":
    print("=" * 60)
    print("Running verification tests...")
    print("=" * 60)

    try:
        test_root()
        test_memory_update()
        test_list_user_memories()
        test_decay()
        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED!")
        print("=" * 60)
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
