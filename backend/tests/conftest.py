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

# 测试不得真调付费模型：预置一个假 provider，并把附件目录指向临时路径。
os.environ["PROVIDERS_DB_PATH"] = os.path.join(_TEST_DATA_DIR, "providers.json")
os.environ["UPLOAD_DIR"] = os.path.join(_TEST_DATA_DIR, "uploads")

# 长期记忆向量库同样是指不走的进程级单例：`memory_router` 导入时就构造
# `MemoryManager()`，它只认 CHROMA_DB_PATH。不指走，本机跑测试时那 71 条真实记忆
# 就在射程内——CI 看不见（CI=true 走内存替身），所以只有人会中招。
# 判据由 tests/test_test_isolation.py 守着，新增可重定向的存储时那里会红。
os.environ["CHROMA_DB_PATH"] = os.path.join(_TEST_DATA_DIR, "chroma_db")

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

    # 与 app.core.streaming.stream_chat 同形状：同步生成器。写成 async 会让端点里的
    # `for chunk in ...` 当场 TypeError，而不是静默放过一次回归。
    def fake_stream(model, messages, provider_id=None, temperature=0.7, max_tokens=4096):
        for piece in ("（", "测试", "回复）"):
            yield piece

    monkeypatch.setattr(streaming, "stream_chat", fake_stream)
    yield


@pytest.fixture(autouse=True)
def _isolated_throttle():
    """限流账本是模块级全局状态，每条用例都必须从空表开始。

    不清的话，任何多打几次坏码的用例（注册端点、以及会话/附件归属这类要猜
    令牌的用例）留下的计数会一路累到阈值上，把一个毫不相干的 assert 200 变成
    429——那时绿就只是算术运气。放在 conftest 而不是某个测试文件里，是因为它
    保护的是整个套件。

    每一本都要清，一本都不能少：两本记失败（登录/撞名、猜找回答案），两本记**成功**
    （注册数、改密数）。记成功那两本尤其经不起带出用例——"这条用例明明什么都没做错、
    只是正常工作了几次"就开始攒账，攒满之后后面随便一条断言 200 的用例会拿到 429；
    而 24 小时窗口那两本当天根本不会自己松开。

    清单从产品模块的 _LEDGERS 现取，不在这里手抄一遍账本名字：上一轮这里就是抄了四本，
    auth_router 新加一本时这份清单不会跟着长——漏掉的那本变成只胀不收，红还会红到离
    真凶很远的用例上。_LEDGERS 本身就是"有哪几本账"的唯一清单（_prune 用的也是它）。
    判据：把下面那行改成只清 list(_LEDGERS)[:3]（正好漏掉 _RESET_FAILS），
    tests/test_auth_endpoints.py 立刻红四条——猜错预算那条与数答案条数那条的两个参数各一
    条，而红的地方离真凶隔着好几条用例。

    只清"进用例"这一次。进出各清一遍等于两处互为备份，于是"漏清一处"这种错误永远测不
    出来（另一处会替它擦干净），而它恰恰是这条 fixture 存在的理由。出去不清也没有代价：
    下一条用例进来时还要再清。
    """
    from app.core.auth_router import _LEDGERS

    for ledger, _window in _LEDGERS:
        ledger.clear()
    yield


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_data():
    yield
    shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)


# 上面那轮 CI 红换来的守卫：一次没还原成功的 monkeypatch 会把
# AUTH_MODE=enforced + ACCESS_TOKEN=boot-token 留满整场，于是三十条与故障毫不
# 相干的用例集体 401，红的地方离真凶隔了六个文件。
_PRISTINE_AUTH_ENV = {k: os.environ.get(k) for k in ("AUTH_MODE", "ACCESS_TOKEN")}
_PREV_TEST = {"nodeid": None}


@pytest.fixture(autouse=True)
def _auth_env_pristine(request):
    """每条用例开跑前，全局鉴权 env 必须还是 conftest 设的那一份。

    换模式请走 enforced / wired / _memory_app 那类 fixture——它们用 monkeypatch，
    会自己还原。这里钉的不是业务行为而是"状态不许带出用例"：还原一旦中途抛，
    泄漏的是整个进程，而第一个撞上它的用例往往离真凶很远。让它红在这里，并且
    把上一个跑过的用例名字说出来——漏还原该只红一条，且当场报出嫌疑人。
    """
    prev = _PREV_TEST["nodeid"]
    _PREV_TEST["nodeid"] = request.node.nodeid
    drifted = {k: os.environ.get(k)
               for k, v in _PRISTINE_AUTH_ENV.items() if os.environ.get(k) != v}
    assert not drifted, (
        f"鉴权 env 带着上一条用例的状态进了这条用例：{drifted}"
        f"（期望 {_PRISTINE_AUTH_ENV}）。嫌疑人是上一条 {prev}——"
        "它的 monkeypatch 收尾多半被异常打断（pytest 的 undo 先回滚 setattr，"
        "再回滚 environ，前者一抛后者就不做了）。")
    yield


@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture
def sample_messages():
    return [{"role": "user", "content": "你好"}]


# 注册请求体现在是三格齐全：用户名、密码、三条固定问题的答案（顺序与
# auth.RECOVERY_QUESTIONS 对齐）。每个测试文件各抄一份迟早会有一份抄漏，
# 于是"少一格也算过"的假绿就回来了。
RECOVERY_FIELDS = {"security_answers": ["新市场小学", "hehai2024", "李建国"]}


@pytest.fixture
def enforced(monkeypatch, tmp_path):
    """真实鉴权路径。

    全局 client 是 disabled 模式，人人都是本机管理员，在那里断言"普通用户
    拿到 403"等于什么都没测。模式已改为请求期读 env，所以只需换 env 与身份库。
    """
    from app.core.auth import AuthStore
    import app.core.authz as authz

    store = AuthStore(path=str(tmp_path / "users.json"))
    monkeypatch.setattr(authz, "auth_store", store)
    monkeypatch.setenv("AUTH_MODE", "enforced")
    monkeypatch.setenv("ACCESS_TOKEN", "boot-token")

    def as_user(username):
        """建一个普通用户，返回携带其令牌的请求头。

        走存储层而不是 HTTP：注册端点按来源限成功数，夹具若从那儿过，
        同一条用例里第 4 个用户就会莫名其妙拿到 429。
        """
        _, token = store.register(username=username, password="correct-horse-battery")
        return {"Authorization": "Bearer " + token}

    return as_user

# 沙箱可用性判断原先抄了三份（test_sandbox_concurrency 一处、test_tools 一处、
# 三个 backend/ 根下的手工脚本各自裸跑）。它决定的是"这条用例到底跑没跑"，
# 抄多了迟早有一份偷偷把 skip 写成通过。
_SANDBOX_IMAGE_BUILD_CMD = (
    "docker build -t ai-sandbox:latest -f docker/sandbox/Dockerfile docker/sandbox")


@pytest.fixture
def sandbox_language():
    """要真容器时先问一句能不能跑：缺什么就说清什么，缺就 skip。

    缺 Docker 不是产品的错，但把"没跑"记成"跑过了"是假的绿；一律 failure 又
    会让每台没装 Docker Desktop 的开发机长红。CI 显式构建 python 镜像
    （见 .github/workflows/tests.yml），所以在 CI 上这些用例是真在跑。
    返回一个 `sandbox_language("python") -> "python"` 形式的函数。
    """
    from app.sandbox.sandbox_manager import SandboxManager

    def _require(language):
        sm = SandboxManager()
        if sm.client is None:
            pytest.skip(f"Docker 守护进程不可用：{sm.unavailable_reason}")
        image = SandboxManager.LANGUAGE_IMAGES[language]
        try:
            sm.client.images.get(image)
        except Exception as e:
            pytest.skip(f"缺少沙箱镜像 {image}（{e}）；构建一次：{_SANDBOX_IMAGE_BUILD_CMD}")
        return language

    return _require
