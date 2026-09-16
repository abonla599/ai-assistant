"""附件上传存储：文本/代码、图片与 PDF。

落盘在 data/uploads/，文件名一律用生成的 uuid，绝不采用客户端给出的路径或
文件名，避免目录穿越与互相覆盖；原始文件名只作为元数据保存用于界面展示。

图片类型按文件头字节判定，不信任浏览器上报的 MIME。PDF 在上传时即抽取为纯文本
再落盘，因此下游的预览与上下文注入不必区分来源格式。
"""
import base64
import json
import os
import threading
import uuid
from datetime import datetime

from app.core.paths import data_root

MAX_TEXT_BYTES = 1 * 1024 * 1024          # 文本/代码 1MB
MAX_IMAGE_BYTES = 10 * 1024 * 1024        # 图片 10MB
MAX_DOC_BYTES = 10 * 1024 * 1024          # 文档类按原始体积计，解析后转文本再截断
MAX_INJECT_CHARS = 20000                  # 注入模型的文本上限，防止长文件撑爆上下文

TEXT_EXTS = {".txt", ".md", ".markdown", ".py", ".js", ".ts", ".json", ".yaml", ".yml",
             ".csv", ".tsv", ".log", ".ini", ".cfg", ".html", ".css", ".c", ".h",
             ".cpp", ".java", ".go", ".rs", ".sh", ".bat", ".ps1", ".sql", ".xml"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
DOC_EXTS = {".pdf"}

# 文件头签名 -> 规范 MIME，避免伪造扩展名或 Content-Type
MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)


def _default_dir() -> str:
    env_path = os.getenv("UPLOAD_DIR")
    if env_path:
        return os.path.abspath(env_path)
    return os.path.join(data_root(), "data", "uploads")


class UploadError(Exception):
    pass


def _pdf_text(blob: bytes) -> str:
    """提取 PDF 全文。fitz 延迟导入：缺少该组件时只让本次上传失败，不拖垮模块。"""
    try:
        import fitz
    except ImportError as e:
        raise UploadError(f"服务器缺少 PDF 解析组件（pip install pymupdf）：{e}")
    try:
        doc = fitz.open(stream=blob, filetype="pdf")
    except Exception as e:
        raise UploadError(f"PDF 无法打开：{type(e).__name__}: {str(e)[:120]}")
    try:
        if doc.needs_pass:
            raise UploadError("该 PDF 已加密，无法提取文本")
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


DOC_EXTRACTORS = {".pdf": _pdf_text}


def detect_kind(filename: str, blob: bytes):
    """返回 (kind, mime)。kind 为 text / image / doc，无法识别则抛 UploadError。"""
    ext = os.path.splitext(filename or "")[1].lower()

    if ext in IMAGE_EXTS:
        mime = next((m for sig, m in MAGIC if blob.startswith(sig)), None)
        if mime is None and blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
            mime = "image/webp"
        if mime is None:
            raise UploadError(f"{filename} 不是有效的图片内容（文件头无法识别）")
        return "image", mime

    if ext in TEXT_EXTS:
        return "text", "text/plain"

    if ext in DOC_EXTS:
        # 内容是否真是 PDF 交给解析器判定，不必再维护一份文件头签名
        return "doc", "application/pdf"

    raise UploadError(
        f"不支持的文件类型 {ext or '(无扩展名)'}。"
        f"文本/代码支持 {len(TEXT_EXTS)} 种扩展名，图片支持 {', '.join(sorted(IMAGE_EXTS))}，"
        f"文档支持 {', '.join(sorted(DOC_EXTS))}")


class UploadStore:
    def __init__(self, directory: str = None):
        self.dir = os.path.abspath(directory or _default_dir())
        self.index_path = os.path.join(self.dir, "index.json")
        self._lock = threading.Lock()
        self._index = {}
        os.makedirs(self.dir, exist_ok=True)
        self._load()

    def _load(self):
        if not os.path.isfile(self.index_path):
            return
        try:
            with open(self.index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._index = data
        except (ValueError, OSError) as e:
            print(f"⚠️ 附件索引损坏，忽略历史附件记录: {e}")

    def _flush(self):
        tmp = self.index_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._index, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.index_path)

    def save(self, filename: str, blob: bytes, claimed_mime: str = "") -> dict:
        if not blob:
            raise UploadError("文件内容为空")

        kind, mime = detect_kind(filename, blob)
        ext = os.path.splitext(filename or "")[1].lower()
        display_size = len(blob)

        limit = {"text": MAX_TEXT_BYTES, "image": MAX_IMAGE_BYTES, "doc": MAX_DOC_BYTES}[kind]
        if len(blob) > limit:
            raise UploadError(
                f"{filename} 超过上限 {limit // 1024 // 1024 or limit // 1024}MB"
                f"（实际 {len(blob) // 1024}KB）")

        if kind == "doc":
            text = DOC_EXTRACTORS[ext](blob)
            if not text.strip():
                raise UploadError(
                    f"{filename} 未提取到文字，可能是扫描版（整页图片）PDF，需要先做 OCR")
            # 转成文本后走既有的预览与注入链路，模型看到的仍是可读文字
            blob, ext, kind, mime = text.encode("utf-8"), ".txt", "text", "text/plain"

        upload_id = uuid.uuid4().hex[:16]
        stored = os.path.join(self.dir, upload_id + ext)

        with open(stored, "wb") as f:
            f.write(blob)

        record = {
            "id": upload_id,
            "name": os.path.basename(filename or "unnamed"),
            "kind": kind,
            "mime": mime or claimed_mime,
            "size": display_size,
            "path": stored,
            "created_at": datetime.now().isoformat(),
        }
        with self._lock:
            self._index[upload_id] = record
            self._flush()
        return self.public(record)

    def get(self, upload_id: str):
        with self._lock:
            record = self._index.get(upload_id)
        if not record or not os.path.isfile(record["path"]):
            return None
        return record

    def public(self, record: dict) -> dict:
        out = {k: record[k] for k in ("id", "name", "kind", "mime", "size", "created_at")}
        if record["kind"] == "text":
            out["preview"] = self.read_text(record["id"], max_chars=400)
        return out

    def read_text(self, upload_id: str, max_chars: int = MAX_INJECT_CHARS) -> str:
        record = self.get(upload_id)
        if not record or record["kind"] != "text":
            return ""
        with open(record["path"], "r", encoding="utf-8", errors="replace") as f:
            text = f.read(max_chars + 1)
        if len(text) > max_chars:
            return text[:max_chars] + f"\n…（已截断，原文件 {record['size']} 字节）"
        return text

    def data_uri(self, upload_id: str):
        record = self.get(upload_id)
        if not record or record["kind"] != "image":
            return None
        with open(record["path"], "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return f"data:{record['mime']};base64,{b64}"

    def delete(self, upload_id: str) -> bool:
        with self._lock:
            record = self._index.pop(upload_id, None)
            if record is None:
                return False
            try:
                os.remove(record["path"])
            except OSError:
                pass
            self._flush()
            return True


store = UploadStore()


def build_user_content(text: str, attachment_ids: list, supports_vision: bool):
    """把附件拼进用户消息。

    文本/代码并入正文文本，这样写进会话历史后追问时上下文不丢；图片只在模型
    声明支持视觉时走多模态数组，否则明确报错——静默丢弃图片会让用户以为模型"看
    不懂"。
    """
    body = (text or "").strip()
    images = []

    for upload_id in attachment_ids or []:
        record = store.get(upload_id)
        if record is None:
            raise UploadError(f"附件不存在或已清理：{upload_id}")
        if record["kind"] == "text":
            block = store.read_text(upload_id)
            body = (body + "\n\n" if body else "") + f"[附件 {record['name']}]\n```\n{block}\n```"
        else:
            images.append(record)

    if images and not supports_vision:
        raise UploadError(
            f"当前模型不支持图片输入（{len(images)} 张附件）。"
            "请在「设置 → 模型服务」改用带视觉的模型，或只发送文本附件。")

    if not images:
        return body

    parts = [{"type": "text", "text": body or "请查看图片"}]
    for record in images:
        parts.append({"type": "image_url",
                      "image_url": {"url": store.data_uri(record["id"])}})
    return parts
