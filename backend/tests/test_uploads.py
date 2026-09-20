"""附件上传与注入测试。"""
import io
import json
import re
import sys
import zipfile
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


# ---------- .docx ----------

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx_blob(document_xml: str, body_part: str = "word/document.xml") -> bytes:
    """手工搭一个 .docx（本质是 zip）。不借 python-docx：测试不能依赖一个
    生产代码并不需要的包，否则测试通过与否和生产环境的行为脱节。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("_rels/.rels", "<Relationships/>")
        z.writestr(body_part, '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                              + document_xml)
    return buf.getvalue()


def _wdoc(body: str) -> str:
    return f'<w:document xmlns:w="{W_NS}"><w:body>{body}</w:body></w:document>'


def _para(text: str) -> str:
    return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"


def test_docx_upload_is_parsed_into_text():
    blob = _docx_blob(_wdoc(
        _para("实验一 网络协议分析")
        + "<w:p><w:r><w:t>目的：</w:t><w:tab/><w:t>掌握 Wireshark 抓包</w:t></w:r></w:p>"
        + "<w:p><w:r><w:t>第一行</w:t><w:br/><w:t>第二行</w:t></w:r></w:p>"
        # 表格单元格里的文字也是 w:p，必须一起读到：实验数据常放在表里
        + '<w:tbl><w:tr><w:tc>' + _para("端口") + '</w:tc><w:tc>' + _para("8080") + '</w:tc></w:tr></w:tbl>'))
    res = _upload("实验报告（实验1）.docx", blob,
                  "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert res.status_code == 200
    body = res.json()
    assert body["kind"] == "text"
    assert body["name"] == "实验报告（实验1）.docx"
    preview = body["preview"]
    assert "实验一 网络协议分析" in preview
    assert "目的：\t掌握 Wireshark 抓包" in preview, "w:tab 丢了会让字段挤成一坨"
    assert "第一行\n第二行" in preview, "w:br 是软换行，不能和下一行粘在一起"
    assert "端口" in preview and "8080" in preview


def test_docx_xml_escapes_are_decoded():
    blob = _docx_blob(_wdoc(_para("a &amp; b &lt;tag&gt; &#26816;&#26597;")))
    preview = _upload("t.docx", blob).json()["preview"]
    assert preview == "a & b <tag> 检查"


def test_legacy_doc_named_docx_tells_the_user_to_resave():
    """老 .doc 是 OLE 复合文档，不是 zip。光说"打不开"没出路，得给出另存为。"""
    res = _upload("旧报告.docx", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 200)
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert "另存为 .docx" in detail


def test_zip_without_document_body_names_the_real_problem():
    """xlsx/pptx 同样是 zip。只说"打不开"会让人以为是文件损坏。"""
    blob = _docx_blob("<x/>", body_part="xl/workbook.xml")
    res = _upload("成绩表.docx", blob)
    assert res.status_code == 400
    assert "缺少正文" in res.json()["detail"]


def test_docx_declaring_an_entity_is_refused():
    """内部实体会被解析器展开（billion laughs），外部实体让服务器去取远端资源。

    Word/WPS 不会写出这种声明，所以拒绝它不损失任何真实文档。
    """
    xml = ('<?xml version="1.0"?><!DOCTYPE w:document [<!ENTITY a "' + "A" * 40 + '">]>'
           + _wdoc(_para("&a;")))
    res = _upload("bomb.docx", _docx_blob(xml))
    assert res.status_code == 400
    assert "实体" in res.json()["detail"]


def test_entity_declaration_hidden_behind_a_long_comment_is_still_refused():
    """正对照：DOCTYPE 前面允许有任意长的注释，只查文件开头会被绕过。"""
    xml = ('<?xml version="1.0"?>' + "<!--" + "x" * 9000 + "-->"
           + '<!DOCTYPE w:document [<!ENTITY a "AAAA">]>' + _wdoc(_para("&a;")))
    res = _upload("sneaky.docx", _docx_blob(xml))
    assert res.status_code == 400
    assert "实体" in res.json()["detail"]


def test_docx_decompressed_bomb_is_refused():
    """原始体积不到 10MB 上限，解压后却有 20MB：zip bomb。

    按解压后的字节数封顶才拦得住——file_size 是 zip 头自报的，改小它就能过关。
    夹具大小写死 20MB、不从 MAX_DOCX_XML_BYTES 反推：否则把上限一抬，夹具跟着
    变成几十 GB 的字符串，测的就不再是这件事了。
    """
    from app.core.uploads import MAX_DOCX_XML_BYTES

    assert MAX_DOCX_XML_BYTES < 20 * 1024 * 1024, \
        f"解压上限已抬到 {MAX_DOCX_XML_BYTES}，这份 20MB 夹具不再是炸弹"
    blob = _docx_blob(_wdoc("<w:p/>" * (20 * 1024 * 1024 // 6 + 1)))
    assert len(blob) < 1024 * 1024, "夹具必须远小于 10MB，否则测的是体积上限而不是炸弹"
    res = _upload("zipbomb.docx", blob)
    assert res.status_code == 400
    assert "远超正常文档" in res.json()["detail"]


def test_image_only_docx_says_why_there_is_no_text():
    blob = _docx_blob(_wdoc(
        '<w:p><w:r><w:drawing>'
        '<wp:inline xmlns:wp="urn:wp"/></w:drawing></w:r></w:p>'))
    res = _upload("扫描件.docx", blob)
    assert res.status_code == 400
    assert "未提取到文字" in res.json()["detail"]


def test_doc_extension_tables_stay_in_step():
    """DOC_EXTS / DOC_MIMES / DOC_EXTRACTORS 三份清单必须同一批扩展名。

    detect_kind 用第一份放行、第二份报 MIME，save() 用第三份取解析器；
    少一个键就是"选择器能选、上传却 500"。
    """
    from app.core.uploads import DOC_EXTS, DOC_EXTRACTORS, DOC_MIMES

    assert DOC_EXTS and set(DOC_EXTS) == set(DOC_MIMES) == set(DOC_EXTRACTORS)
    # 正对照：故意漏一个键时上面这条必须变红
    assert ".docx" in DOC_EXTS and ".pdf" in DOC_EXTS


# ---------- 附件怎么进模型 ----------

def test_image_without_a_vision_model_is_an_explicit_refusal():
    """界面上那句"当前模型不支持图片输入"就是这道闸门。

    静默把图丢掉更糟：用户会以为模型看不懂这张图，而不是自己选的模型根本读不了图。
    """
    from app.core.uploads import UploadError, build_user_content, store as upload_store

    rec = upload_store.save("shot.png", FAKE_PNG, "image/png", owner="u_gate")
    with pytest.raises(UploadError) as e:
        build_user_content("解读一下", [rec["id"]], False, owner="u_gate")
    assert "不支持图片输入" in str(e.value)
    assert "1 张" in str(e.value), "得说清被拦下几张，否则多张附件要一张张试"


def test_image_with_a_vision_model_reaches_the_model_as_a_data_uri():
    from app.core.uploads import build_user_content, store as upload_store

    rec = upload_store.save("shot.png", FAKE_PNG, "image/png", owner="u_ok")
    content = build_user_content("解读一下", [rec["id"]], True, owner="u_ok")
    assert isinstance(content, list), "带图必须是多模态数组，不能还是纯字符串"
    assert content[0] == {"type": "text", "text": "解读一下"}
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_text_and_image_attachments_coexist_in_one_turn():
    from app.core.uploads import build_user_content, store as upload_store

    txt = upload_store.save("数据.csv", b"a,b\n1,2", "text/csv", owner="u_mix")
    img = upload_store.save("shot.png", FAKE_PNG, "image/png", owner="u_mix")
    content = build_user_content("对照看看", [txt["id"], img["id"]], True, owner="u_mix")
    assert content[0]["text"].startswith("对照看看")
    assert "数据.csv" in content[0]["text"] and "a,b" in content[0]["text"]
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


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


def test_picker_offers_every_type_the_server_accepts():
    """反方向也要对上：后端支持的扩展名，选择器必须列出来。

    两边各留一份清单迟早对不上——后端加了 .docx 而 accept 没加时，桌面浏览器
    会把 .docx 直接过滤掉，用户以为功能没做；而在安卓壳里 accept 常被忽略，
    选得上却换来一句"不支持的文件类型"（2026-09-20 就是这么撞上的）。
    """
    from app.core.uploads import DOC_EXTS, TEXT_EXTS

    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    offered = set(_accept_of(html, "fileInput"))
    assert offered == TEXT_EXTS | DOC_EXTS, (
        f"选择器缺 {sorted((TEXT_EXTS | DOC_EXTS) - offered)}、"
        f"多 {sorted(offered - (TEXT_EXTS | DOC_EXTS))}")


def test_file_picker_does_not_offer_images():
    """图片有专门的入口，混在文件里会让用户不知道模型能不能看懂。"""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    image_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
    assert not image_exts & set(_accept_of(html, "fileInput"))


# ---------- 坏索引必须先留证再重启：附件索引是唯一那份 id→文件 映射 ----------
# 这里曾经只 print 一句"忽略历史附件记录"就以空表启动，而下一次 save() 会把只有
# 一条记录的索引写回 index.json：历史附件的文件还在磁盘上，却再也认不出属主与原
# 文件名，等于把"索引坏了"升级成"所有人上传过的东西全丢了"。session_store 与
# auth 两个兄弟存储都是先改名 .corrupt，这条对齐由下面两个用例钉住。

BROKEN_INDEXES = [
    ('{"abc": {"name": "合同.pdf", "owner": "u_1", "tru', "半截 JSON"),
    ('[{"id": "abc", "name": "合同.pdf"}]', "读得懂但顶层是列表"),
]


@pytest.mark.parametrize("broken,desc", BROKEN_INDEXES)
def test_broken_index_is_quarantined_not_overwritten(tmp_path, broken, desc):
    """两种坏法都必须改名留证；第二例是重点——`json.load` 不抛错，except 抓不到它。"""
    from app.core.uploads import UploadStore

    d = tmp_path / "uploads"
    d.mkdir()
    (d / "index.json").write_text(broken, encoding="utf-8")

    store = UploadStore(directory=str(d))
    assert (d / "index.json.corrupt").exists(), f"{desc}：必须先备份成 .corrupt"
    assert not (d / "index.json").exists(), f"{desc}：原件要让位，不能留在原地等着被覆盖"
    assert store._index == {}, f"{desc}：仍然以空表启动，服务不该因此起不来"

    # 真正的判据在这一行之后：重启后的第一次上传写的是新文件，
    # 而那份认不出来的历史仍完整可读——运维还有得救。
    store.save("笔记.txt", "内容".encode("utf-8"), "text/plain", owner="u_new")
    assert (d / "index.json.corrupt").read_text(encoding="utf-8") == broken, \
        f"{desc}：备份必须活过一次 save()"
    fresh = json.loads((d / "index.json").read_text(encoding="utf-8"))
    assert [r["owner"] for r in fresh.values()] == ["u_new"]
