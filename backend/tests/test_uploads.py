"""附件上传与注入测试。"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
FAKE_PNG = PNG_MAGIC + b"\x00" * 64


def _upload(name, content, mime="application/octet-stream"):
    return client.post("/v1/uploads", files={"file": (name, content, mime)})


def test_text_file_upload_returns_preview():
    res = _upload("notes.txt", "第一行内容\n第二行内容".encode(), "text/plain")
    assert res.status_code == 200
    body = res.json()
    assert body["kind"] == "text"
    assert "第一行内容" in body["preview"]


def test_image_upload_detected_by_magic_bytes():
    body = _upload("shot.png", FAKE_PNG, "image/png").json()
    assert body["kind"] == "image"
    assert body["mime"] == "image/png"


def test_file_renamed_to_png_is_rejected():
    """只信扩展名会让任意字节伪装成图片，必须按文件头判定。"""
    res = _upload("evil.png", b"not really an image at all", "image/png")
    assert res.status_code == 400
    assert "图片" in res.json()["detail"]


def test_unsupported_extension_rejected():
    res = _upload("payload.exe", b"MZ\x90\x00" + b"\x00" * 40)
    assert res.status_code == 400
    assert ".exe" in res.json()["detail"]


def test_oversized_text_rejected():
    res = _upload("big.txt", b"a" * (1 * 1024 * 1024 + 10), "text/plain")
    assert res.status_code == 400
    assert "超过上限" in res.json()["detail"]


def test_uploaded_filename_cannot_escape_storage():
    """落盘文件名一律用生成的 uuid，不接受客户端给的路径。"""
    res = _upload("../../evil.txt", b"escape attempt", "text/plain")
    assert res.status_code == 200
    assert "/" not in res.json()["id"] and ".." not in res.json()["id"]


def test_text_attachment_is_injected_into_model_message(monkeypatch):
    """文本附件必须真的进入发给模型的消息，否则"上传了"只是界面假象。"""
    from types import SimpleNamespace
    import app.pipeline as pipeline
    captured = {}

    def create(**kwargs):
        captured["messages"] = kwargs["messages"]
        msg = SimpleNamespace(content="已收到附件", tool_calls=None,
                              model_dump=lambda: {"role": "assistant", "content": "已收到附件"})
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(pipeline, "build_client", lambda provider: fake)

    up = _upload("code.py", b"def hello():\n    return 1\n", "text/plain").json()
    res = client.post("/v1/chat", json={
        "model": "fake-model",
        "messages": [{"role": "user", "content": "这段代码有什么问题"}],
        "attachments": [up["id"]],
    })
    assert res.status_code == 200
    sent = captured["messages"][-1]["content"]
    assert "def hello()" in sent
    assert "code.py" in sent


def test_image_rejected_when_model_has_no_vision():
    """不支持视觉时必须明确报错，静默丢图会让用户以为模型看不懂。"""
    up = _upload("shot.png", FAKE_PNG, "image/png").json()
    res = client.post("/v1/chat", json={
        "model": "fake-model",   # conftest 里 supports_vision=False
        "messages": [{"role": "user", "content": "图里是什么"}],
        "attachments": [up["id"]],
    })
    assert res.status_code == 400
    assert "不支持图片" in res.json()["detail"]


def test_missing_attachment_id_reports_error():
    res = client.post("/v1/chat", json={
        "model": "fake-model",
        "messages": [{"role": "user", "content": "hi"}],
        "attachments": ["deadbeefdeadbeef"],
    })
    assert res.status_code == 400
    assert "附件" in res.json()["detail"]


def test_attachment_deleted_and_download_roundtrip():
    up = _upload("a.md", "# 标题".encode("utf-8"), "text/markdown").json()
    got = client.get(f"/v1/uploads/{up['id']}/file")
    assert got.status_code == 200
    assert "标题" in got.text
    assert client.delete(f"/v1/uploads/{up['id']}").status_code == 200
    assert client.get(f"/v1/uploads/{up['id']}/file").status_code == 404


# ---------- PDF ----------

def _make_pdf(text: str) -> bytes:
    fitz = pytest.importorskip("fitz", reason="PDF 支持需要 pymupdf")
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), text, fontname="helv")
    blob = doc.tobytes()
    doc.close()
    return blob


def test_pdf_upload_is_parsed_into_text():
    blob = _make_pdf("quantum key distribution")
    res = _upload("论文.pdf", blob, "application/pdf")
    assert res.status_code == 200
    body = res.json()
    assert body["kind"] == "text"
    # 界面上仍显示原文件名，用户不必知道自己传的不是 txt
    assert body["name"] == "论文.pdf"
    assert "quantum key distribution" in body["preview"]


def test_pdf_size_reported_as_original_file():
    """转换后只剩几 KB 文本，若按转换后计体积会误导用户。"""
    blob = _make_pdf("hello")
    body = _upload("doc.pdf", blob, "application/pdf").json()
    assert body["size"] == len(blob)


def test_garbage_named_pdf_is_rejected():
    res = _upload("evil.pdf", b"definitely not a pdf", "application/pdf")
    assert res.status_code == 400
    assert "PDF" in res.json()["detail"]


# ---------- 前端 accept 与后端白名单必须一致 ----------

STATIC_DIR = Path(__file__).resolve().parent.parent / "app" / "web" / "static"
# 图片按钮用 MIME，后端用扩展名，这里做一层桥接
IMAGE_MIME_TO_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
                     "image/webp": ".webp", "image/bmp": ".bmp"}


def _accept_of(html: str, element_id: str) -> list:
    tag = re.search(rf'<input[^>]*id="{element_id}"[^>]*>', html)
    assert tag, f"index.html 里找不到 #{element_id}"
    accept = re.search(r'accept="([^"]+)"', tag.group(0))
    assert accept, f"#{element_id} 没有 accept，系统选择器会列出全部类型"
    return [t.strip() for t in accept.group(1).split(",") if t.strip()]


@pytest.mark.parametrize("element_id", ["imageInput", "fileInput"])
def test_picker_accept_only_offers_types_the_server_accepts(element_id):
    """选择器里能选中的东西，后端必须真的收得下。

    放开一个后端不支持的类型，比干脆不提供更糟：用户费事选完文件，
    换来的是一句"不支持的文件类型"。
    """
    from app.core.uploads import IMAGE_EXTS, detect_kind

    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    tokens = _accept_of(html, element_id)
    assert tokens, f"#{element_id} 的 accept 为空"

    for token in tokens:
        if element_id == "imageInput":
            assert token in IMAGE_MIME_TO_EXT, f"图片选择器出现后端不认的 {token}"
            assert IMAGE_MIME_TO_EXT[token] in IMAGE_EXTS
        else:
            assert token.startswith("."), f"文件选择器只应列扩展名，出现 MIME {token}"
            detect_kind("sample" + token, b"arbitrary bytes")   # 不抛错即后端接受


def test_file_picker_does_not_offer_images():
    """图片有专门的入口，混在文件里会让用户不知道模型能不能看懂。"""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    image_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
    assert not image_exts & set(_accept_of(html, "fileInput"))
