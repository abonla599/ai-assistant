"""
测试会话管理 API
"""
import pytest
import sys
from pathlib import Path

# 添加 backend 目录到 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


@pytest.fixture
def session_id():
    """创建测试会话并返回 session_id"""
    response = client.post("/v1/sessions?model=deepseek-chat")
    assert response.status_code == 200
    data = response.json()
    # 兼容两种返回格式
    if "success" in data:
        assert data["success"] == True
        sid = data.get("data", {}).get("session_id")
    else:
        sid = data.get("session_id")
    assert sid is not None, f"无法获取 session_id，响应数据: {data}"
    yield sid
    # 清理：删除会话
    try:
        client.delete(f"/v1/sessions/{sid}")
    except:
        pass


def test_create_session():
    """测试创建会话"""
    response = client.post("/v1/sessions?model=deepseek-chat")
    assert response.status_code == 200
    data = response.json()
    # 兼容两种返回格式
    if "success" in data:
        assert data["success"] == True
        assert "session_id" in data["data"]
        print(f"✅ 创建会话成功: {data['data']['session_id']}")
    else:
        assert "session_id" in data
        print(f"✅ 创建会话成功: {data['session_id']}")


def test_list_sessions():
    """测试获取会话列表"""
    response = client.get("/v1/sessions")
    assert response.status_code == 200
    data = response.json()
    # 兼容两种返回格式
    if "success" in data:
        assert data["success"] == True
        assert isinstance(data["data"], list)
        print(f"✅ 会话列表: {len(data['data'])} 个会话")
    else:
        assert "sessions" in data
        assert isinstance(data["sessions"], list)
        print(f"✅ 会话列表: {len(data['sessions'])} 个会话")


def test_get_session(session_id):
    """测试获取会话详情"""
    response = client.get(f"/v1/sessions/{session_id}")
    assert response.status_code == 200
    data = response.json()
    if "success" in data:
        assert data["success"] == True
        assert data["data"]["session_id"] == session_id
        print(f"✅ 获取会话详情成功: {data['data']['title']}")
    else:
        assert data["session_id"] == session_id
        print(f"✅ 获取会话详情成功: {data.get('title', 'N/A')}")


def test_chat_with_session(session_id):
    """测试使用 session_id 发送消息"""
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "user", "content": "你好，请做个自我介绍"}
        ],
        "session_id": session_id
    }
    response = client.post("/v1/chat", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "reply" in data
    print(f"✅ 聊天回复: {data['reply'][:50]}...")


def test_get_session_after_chat(session_id):
    """测试聊天后会话消息是否正确保存"""
    # 先发送一条消息
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "user", "content": "测试消息"}
        ],
        "session_id": session_id
    }
    response = client.post("/v1/chat", json=payload)
    assert response.status_code == 200
    
    # 验证消息已保存
    response = client.get(f"/v1/sessions/{session_id}")
    assert response.status_code == 200
    data = response.json()
    
    # 兼容两种返回格式
    session_data = data if "session_id" in data else data.get("data", {})
    messages = session_data.get("messages", [])
    assert len(messages) >= 2, f"期望至少2条消息（一问一答），实际: {len(messages)}"
    print(f"✅ 会话消息数: {len(messages)}")
    for msg in messages:
        print(f"   [{msg['role']}] {msg['content'][:50]}...")


def test_chat_without_session():
    """测试不传 session_id 的情况"""
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "user", "content": "1+1等于几？"}
        ]
    }
    response = client.post("/v1/chat", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "reply" in data
    print(f"✅ 无会话聊天成功: {data['reply'][:50]}...")


def test_delete_session(session_id):
    """测试删除会话"""
    response = client.delete(f"/v1/sessions/{session_id}")
    assert response.status_code == 200
    data = response.json()
    if "success" in data:
        assert data["success"] == True
    else:
        assert data["status"] == "deleted"
    print(f"✅ 会话已删除: {session_id}")


# ---------- 导出下载：一次性票据 ----------
# 形态承诺：壳 APK 的 WebView 收不到 blob: 下载、也不带 Authorization 头，所以
# 导出换成"链接本身即凭据"。这组用例钉的是这个承诺的四个角：格式与前端逐字一致、
# 一票一问、两种失败同形、签发要归属而兑换免登录。

from app.session.export_store import (EXPORT_PATH_PREFIX, ExportTicketStore,
                                      TICKET_ID_CHARS, TICKET_ID_RE)


def _make_session(c, messages, headers=None):
    sid = c.post("/v1/sessions?model=deepseek-chat", headers=headers or {}).json()["session_id"]
    put = c.put(f"/v1/sessions/{sid}/messages", headers=headers or {},
                json={"messages": messages})
    assert put.status_code == 200, put.text
    return sid


def _issue_ticket(c, sid, headers=None):
    """签发并顺手钉住响应形状：path 是"前缀 + 恰好一段票据形状"，5 分钟。"""
    res = c.post(f"/v1/sessions/{sid}/export-ticket", headers=headers or {})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["expires_in"] == 300
    assert body["path"].startswith(EXPORT_PATH_PREFIX), body
    assert TICKET_ID_RE.fullmatch(body["path"][len(EXPORT_PATH_PREFIX):]), \
        f"票据必须是 {TICKET_ID_CHARS} 个 base64url 字符，实际 {body['path']!r}"
    return body["path"]


def test_export_ticket_redeems_the_frontends_markdown_verbatim(client):
    """兑换回来的正文要与 app.js exportCurrent() 逐字节一致。

    标题走 titleOf（空白折叠、取 24 字）再 safeFilename；消息块是
    "**我**：\\n\\n内容\\n" 用 \\n 串起来。少写一个换行，手机上两份导出就对不上。
    """
    sid = _make_session(client, [
        {"role": "user", "content": "帮我写个\n诗"},
        {"role": "assistant", "content": "好。\n\n床前明月光"},
    ])
    path = _issue_ticket(client, sid)

    got = client.get(path)
    assert got.status_code == 200, got.text
    assert got.headers["content-type"] == "text/markdown; charset=utf-8"
    cd = got.headers["content-disposition"]
    assert "attachment" in cd and "filename*=" in cd
    assert cd == ("attachment; filename=\"chat.md\"; "
                  "filename*=UTF-8''%E5%B8%AE%E6%88%91%E5%86%99%E4%B8%AA%20%E8%AF%97.md"), \
        "中文标题必须走 RFC 5987 的百分号编码，回退位固定 ASCII，逐字钉死"
    assert got.text.startswith("# ")
    assert got.text == (
        "# 帮我写个 诗\n"
        "\n"
        "**我**：\n"
        "\n"
        "帮我写个\n"
        "诗\n"
        "\n"
        "**助手**：\n"
        "\n"
        "好。\n"
        "\n"
        "床前明月光\n")


def test_export_ticket_is_single_use(client):
    sid = _make_session(client, [{"role": "user", "content": "一次性"}])
    path = _issue_ticket(client, sid)
    assert client.get(path).status_code == 200
    second = client.get(path)
    assert second.status_code == 404, "同一张票据兑换第二次必须不再成交"


def test_used_up_never_issued_and_orphaned_tickets_answer_word_for_word_the_same(client):
    """三种落空（已兑换 / 从没这张票 / 签发后会话被删）必须同码同话。

    区分开就把这个端点养成了"这条链接是否真存在过"的探测器，而免登录正是它
    的设计前提——探测面比带鉴权的路由还宽。断言照 test_stream_api 那条的写法。
    """
    sid = _make_session(client, [{"role": "user", "content": "同形"}])
    path = _issue_ticket(client, sid)
    assert client.get(path).status_code == 200
    used = client.get(path)

    never = client.get(EXPORT_PATH_PREFIX + "Z" * TICKET_ID_CHARS)
    assert (never.status_code, never.json()) == (404, {"detail": "导出链接无效或已过期"}), never.text
    assert never.text == used.text, "「没这张票」与「已兑换」不许听出差别"

    sid2 = _make_session(client, [{"role": "user", "content": "签完就删"}])
    path2 = _issue_ticket(client, sid2)
    assert client.delete(f"/v1/sessions/{sid2}").status_code == 200
    orphan = client.get(path2)
    assert orphan.status_code == 404, "会话没了，票据也不许换个说法"
    assert orphan.text == used.text, "「票据有效但会话已删」这句话本身就是泄露"


def test_cannot_sign_an_export_ticket_for_a_foreign_session(client, enforced):
    """签发端点的归属口径必须与整条 /v1 会话面逐字同一份实现。"""
    boot = {"Authorization": "Bearer boot-token"}
    sid = client.post("/v1/sessions", headers=boot).json()["session_id"]
    # 前提：这个 id 真的存在，否则下面的 404 就又退化成"查不到"。
    assert client.get(f"/v1/sessions/{sid}", headers=boot).status_code == 200

    stranger = enforced("路人")
    foreign = client.post(f"/v1/sessions/{sid}/export-ticket", headers=stranger)
    assert foreign.status_code == 404, foreign.text
    absent = client.post("/v1/sessions/nonexistent-id/export-ticket", headers=stranger)
    assert (absent.status_code, absent.json()) == (404, {"detail": "会话不存在"}), absent.text
    assert absent.text == foreign.text, "「不是你的」与「不存在」必须逐字相同"


def test_redemption_reaches_without_credentials_issuance_does_not(client, enforced):
    """壳 APK 的下载请求没有任何头：兑换必须真的免凭据可达；签发必须不行。"""
    owner = enforced("导出的人")
    sid = _make_session(client, [{"role": "user", "content": "只给链接就能拿"}], headers=owner)
    path = _issue_ticket(client, sid, headers=owner)

    anon = client.get(path)                     # 不带任何 Authorization
    assert anon.status_code == 200, f"兑换竟要凭据，壳里还是存不下文件：{anon.status_code} {anon.text}"
    assert "只给链接就能拿" in anon.text

    anon_issue = client.post(f"/v1/sessions/{sid}/export-ticket")
    assert anon_issue.status_code == 401, "签发免了登录，票据就成了人人都能签的样子"


def test_a_title_carrying_header_payload_never_reaches_the_response_headers(client):
    """标题是用户可控文本。把 \r\n 原样拼进响应头就是响应头注入。

    TestClient 对带裸 CRLF 的头值会当场炸——所以"能拿到 200"本身就说明注入没发生；
    再加两条：等号被 quote 成 %3D，载荷没法以原形出现在头里。
    """
    evil = "x\r\nSet-Cookie: pwn=1"
    sid = _make_session(client, [{"role": "user", "content": evil}])
    path = _issue_ticket(client, sid)

    got = client.get(path)
    assert got.status_code == 200, got.text
    cd = got.headers["content-disposition"]
    assert "\r" not in cd and "\n" not in cd
    assert "pwn=1" not in cd, "标题里的载荷以原形进了头：quote/RFC 5987 那条路没走通"


def test_the_ticket_store_expires_purges_lazily_and_burns_on_redemption():
    """存储层的三条承诺，不靠睡 300 秒来测（clock 就是为此存在的缝）。"""
    now = {"t": 1000.0}
    store = ExportTicketStore(ttl=300, clock=lambda: now["t"])
    t1 = store.issue("s1", "o1")
    store.issue("s2", "o2")
    assert store.pending_count() == 2

    now["t"] += 301                              # 两张都过期了
    t3 = store.issue("s3", "o3")                 # 签发顺带剔掉过期的，不起后台线程
    assert store.pending_count() == 1, "惰性清理没跟上，过期票据在表里越积越多"
    assert store.consume(t1) is None, "过期票据兑换必须像从没存在过"
    assert store.consume(t3) == ("s3", "o3")
    assert store.consume(t3) is None, "一票一问：第二次兑换与从没存在过同形"


if __name__ == "__main__":
    print("=" * 60)
    print("开始测试会话管理 API")
    print("=" * 60)

    # 1. 创建会话
    response = client.post("/v1/sessions?model=deepseek-chat")
    assert response.status_code == 200
    data = response.json()
    session_id = data.get("session_id") or data.get("data", {}).get("session_id")
    print(f"✅ 创建会话成功: {session_id}")

    # 2. 获取会话列表
    test_list_sessions()

    # 3. 获取会话详情
    test_get_session(session_id)

    # 4. 发送消息（带 session_id）
    test_chat_with_session(session_id)

    # 5. 验证消息已保存
    test_get_session_after_chat(session_id)

    # 6. 发送消息（不带 session_id）
    test_chat_without_session()

    # 7. 删除会话
    test_delete_session(session_id)

    print("=" * 60)
    print("✅ 所有测试通过！")
    print("=" * 60)