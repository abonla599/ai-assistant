# backend/tests/conftest.py
import os
import shutil
import sys
import tempfile
from pathlib import Path

# 把 backend 目录加进 sys.path（和 test_memory.py 一样的做法）
backend_path = Path(__file__).resolve().parent.parent
if str(backend_path) not in sys.path:
    sys.path.insert(0, str(backend_path))

# 会话已持久化到磁盘，必须让测试写临时文件，否则会污染应用真实的会话列表。
# SessionStore 在导入 app.main 时即构造，因此这行必须早于下面的 import。
_TEST_DATA_DIR = tempfile.mkdtemp(prefix="ai-assistant-tests-")
os.environ["SESSION_DB_PATH"] = os.path.join(_TEST_DATA_DIR, "sessions.json")

import pytest
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_data():
    yield
    shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)


@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture
def sample_messages():
    return [{"role": "user", "content": "你好"}]