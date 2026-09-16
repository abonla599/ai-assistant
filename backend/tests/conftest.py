# backend/tests/conftest.py
import json
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
# SessionStore 在导入 app.main 时即构造，因此这些设置必须早于下面的 import。
_TEST_DATA_DIR = tempfile.mkdtemp(prefix="ai-assistant-tests-")
os.environ["SESSION_DB_PATH"] = os.path.join(_TEST_DATA_DIR, "sessions.json")

# 测试统一以"本机管理员"运行：既无需真凭据，也保持既有断言不变。
# 鉴权本身的分支（401/403/503/enabled）由 test_authz_failclosed.py 与
# enforced 覆盖。
os.environ["AUTH_MODE"] = "disabled"
os.environ["ACCESS_TOKEN"] = ""

# 身份库也是进程级单例。不指到临时目录，测试就会写进用户真实的
# data/users.json —— 那是越出本次改动范围的外部副作用。
os.environ["USERS_DB_PATH"] = os.path.join(_TEST_DATA_DIR, "users.json")
os.environ["INVITES_DB_PATH"] = os.path.join(_TEST_DATA_DIR, "invites.json")

# 测试不得真调付费模型：预置一个假 provider，并把附件目录指向临时路径。
os.environ["PROVIDERS_DB_PATH"] = os.path.join(_TEST_DATA_DIR, "providers.json")
os.environ["UPLOAD_DIR"] = os.path.join(_TEST_DATA_DIR, "uploads")
with open(os.environ["PROVIDERS_DB_PATH"], "w", encoding="utf-8") as _f:
    json.dump([{
        "id": "fake-model", "label": "测试模型", "base_url": "https://example.invalid/v1",
        "api_key": "sk-test-000111222333", "model": "fake-chat",
        "supports_vision": False, "is_default": True,
    }], _f)

import pytest
from fastapi.testclient import TestClient
from app.main import app


class _FakeMessage:
    def __init__(self, content):
        self.content = content
        self.tool_calls = None

    def model_dump(self):
        return {"role": "assistant", "content": self.content}


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)
        self.delta = type("D", (), {"content": content})()


class _FakeCompletions:
    def create(self, **kwargs):
        content = "（测试回复）"
        return type("R", (), {"choices": [_FakeChoice(content)]})()


class _FakeChat:
    completions = _FakeCompletions()


class _FakeClient:
    chat = _FakeChat()


@pytest.fixture(autouse=True)
def _stub_llm_calls(monkeypatch):
    """把所有真实模型调用打桩：测试不应消耗额度，也不应因上游故障变红。"""
    import app.core.streaming as streaming
    import app.core.llm_client as llm_client
    import app.pipeline as pipeline
    import app.feedback_storage as feedback_storage
    import app.preference_analyzer as preference_analyzer

    # 反馈/偏好是相对路径常量，不重定向会写进仓库真实数据文件
    monkeypatch.setattr(feedback_storage, "FEEDBACK_FILE",
                        os.path.join(_TEST_DATA_DIR, "feedback.json"))
    monkeypatch.setattr(preference_analyzer, "FEEDBACK_FILE",
                        os.path.join(_TEST_DATA_DIR, "feedback.json"))
    monkeypatch.setattr(preference_analyzer, "PREFERENCE_FILE",
                        os.path.join(_TEST_DATA_DIR, "preference.txt"))

    monkeypatch.setattr(pipeline, "build_client", lambda provider: _FakeClient())
    monkeypatch.setattr(llm_client, "build_client", lambda provider: _FakeClient())

    async def fake_stream(model, messages, provider_id=None, temperature=0.7, max_tokens=4096):
        for piece in ("（", "测试", "回复）"):
            yield piece

    monkeypatch.setattr(streaming, "stream_chat", fake_stream)
    yield


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


@pytest.fixture
def enforced(monkeypatch, tmp_path):
    """真实鉴权路径。

    全局 client 是 disabled 模式，人人都是本机管理员，在那里断言"普通用户
    拿到 403"等于什么都没测。模式已改为请求期读 env，所以只需换 env 与身份库。
    """
    from app.core.auth import AuthStore
    import app.core.authz as authz

    store = AuthStore(path=str(tmp_path / "users.json"),
                      invites_path=str(tmp_path / "invites.json"))
    monkeypatch.setattr(authz, "auth_store", store)
    monkeypatch.setenv("AUTH_MODE", "enforced")
    monkeypatch.setenv("ACCESS_TOKEN", "boot-token")

    def as_user(username):
        """注册一个普通用户，返回携带其令牌的请求头。"""
        code = store.create_invite("admin")
        _, token = store.register(code=code, username=username)
        return {"Authorization": "Bearer " + token}

    return as_user