"""附件上传存储：文本/代码、图片与 PDF。

落盘在 data/uploads/，文件名一律用生成的 uuid，绝不采用客户端给出的路径或
文件名，避免目录穿越与互相覆盖；原始文件名只作为元数据保存用于界面展示。

图片类型按文件头字节判定，不信任浏览器上报的 MIME。PDF 在上传时即抽取为纯文本
再落盘，因此下游的预览与上下文注入不必区分来源格式。

每条索引记录带 owner：这里存的是别人上传的合同、账单、论文，索引一旦共用，
"知道 id"就等于"拿到文件"，所以读、下载、删除都必须先过归属。
"""
import base64
import json
import os
import shutil
import threading
import uuid
from datetime import datetime

from app.core.paths import data_root
# 历史数据认给谁，这个决定只能有一份：会话与附件同属"身份层之前建的东西"，
# 两处各写一个字符串迟早会对不上（对上不了的话，老用户的附件就永远找不回了）。
# 这里只借常量，不依赖 SessionStore 的任何行为。
from app.session.session_store import SessionStore

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
    # 与 SessionStore 同一个常量：迁移只负责把历史数据认给 bootstrap 身份，
    # 它不是任何方法的默认参数（见 save/get/delete 的 owner）。
    LEGACY_OWNER = SessionStore.LEGACY_OWNER

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
        except (ValueError, OSError) as e:
            self._quarantine(f"附件索引损坏（{e}）")
            return
        if not isinstance(data, dict):
            # 能 parse、但顶层不是对象（列表/字符串/数字）：上面那行 except 抓不到它。
            self._quarantine(f"附件索引形状不对（顶层是 {type(data).__name__}，应为对象）")
            return
        self._index = data
        self._backfill_owner()

    def _quarantine(self, why: str):
        """把读不懂的索引先挪开，再允许以空表启动。

        为什么不能只是 print 一句然后继续：`index.json` 是**唯一**的
        id → 磁盘文件 映射，条目里存着 path、owner、原始文件名。以空表启动后，
        下一次 save() 就会 _flush() 出一份只有一条记录的索引——所有历史附件的
        文件还在磁盘上，却再没有任何人认得出该把谁的文件给谁，也没法再找回属主
        （归属校验会把它们一律当成不存在）。既不备份也不拒绝，等于把"索引坏了"
        升级成"附件全丢了"。所以这里与 session_store、auth 两个兄弟存储对齐：
        改名留证 + 显式告警。
        """
        backup = self.index_path + ".corrupt"
        try:
            os.replace(self.index_path, backup)
            print(f"⚠️ {why}，已备份为 {backup}，历史附件本次不可见，"
                  f"请从该备份文件恢复后再重启（数据文件 {self.index_path}）")
        except OSError as e:
            print(f"⚠️ {why}，但备份失败（{e}）：{self.index_path} 未被挪走，"
                  "请先手工复制一份出来再重启，否则下一次上传会覆盖它")

    def _backfill_owner(self):
        """身份层之前的附件都是本机管理员自己传的，认给他。

        只在真的缺 owner 时执行一次，字段齐了就一个字节都不写；备份或原子写回
        失败一律向上抛，进程就不启动——带着半迁移的索引对外服务，等于让"没有
        owner"的记录被归属校验静默放行。

        ⚠️ 本方法是导入期跑的：模块级 `store = UploadStore()` 一被执行就迁移
        $UPLOAD_DIR（未设置时是仓库真实的 data/uploads/）。见该行的注释。
        """
        # 畸形记录要报错给人看，别留 `record["owner"] = ...` 那句
        # "TypeError: 'str' object does not support item assignment"：冻结成 EXE
        # 之后它既不写文件名也不说哪条记录，等于没法排查。
        for upload_id, record in self._index.items():
            if not isinstance(record, dict):
                raise ValueError(
                    f"附件索引 {self.index_path} 中的记录 {upload_id!r} 不是对象（实际是 "
                    f"{type(record).__name__}），无法补 owner。请修复或还原该文件："
                    "宁可拒绝启动，也不带着认不出归属的索引对外服务。")
        # 判据是 not r.get("owner")，不是 "owner" not in r：手工写成
        # {"owner": null} 的记录也算没迁完，放过去就永远认不回属主。
        missing = [r for r in self._index.values() if not r.get("owner")]
        if not missing:
            return
        backup = f"{self.index_path}.bak-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        shutil.copy2(self.index_path, backup)
        for record in missing:
            record["owner"] = self.LEGACY_OWNER
        self._flush()
        # 数据文件的绝对路径进日志：是不是动了用户真实的 data/，第一眼就能看出来。
        print(f"🧭 已为 {len(missing)} 条历史附件补 owner={self.LEGACY_OWNER}"
              f"（数据文件 {self.index_path}），原件备份于 {backup}")

    def _flush(self):
        tmp = self.index_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._index, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.index_path)

    def save(self, filename: str, blob: bytes, claimed_mime: str = "",
             *, owner: str) -> dict:
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
            "owner": owner,
            "created_at": datetime.now().isoformat(),
        }
        with self._lock:
            self._index[upload_id] = record
            self._flush()
        return self.public(record)

    def get(self, upload_id: str, owner: str):
        """非属主拿到 None，与"附件不存在"同一句话：id 是 16 位十六进制，
        403 就等于把"这个 id 是真的"送出去。"""
        with self._lock:
            record = self._index.get(upload_id)
        if not record or record.get("owner") != owner:
            return None
        if not os.path.isfile(record["path"]):
            return None
        return record

    def public(self, record: dict) -> dict:
        out = {k: record[k] for k in ("id", "name", "kind", "mime", "size", "created_at")}
        if record["kind"] == "text":
            out["preview"] = self.read_text(record["id"], owner=record["owner"],
                                            max_chars=400)
        return out

    def read_text(self, upload_id: str, *, owner: str,
                  max_chars: int = MAX_INJECT_CHARS) -> str:
        record = self.get(upload_id, owner)
        if not record or record["kind"] != "text":
            return ""
        with open(record["path"], "r", encoding="utf-8", errors="replace") as f:
            text = f.read(max_chars + 1)
        if len(text) > max_chars:
            return text[:max_chars] + f"\n…（已截断，原文件 {record['size']} 字节）"
        return text

    def data_uri(self, upload_id: str, *, owner: str):
        record = self.get(upload_id, owner)
        if not record or record["kind"] != "image":
            return None
        with open(record["path"], "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return f"data:{record['mime']};base64,{b64}"

    def delete(self, upload_id: str, owner: str) -> bool:
        if self.get(upload_id, owner) is None:
            return False
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


# ⚠️ 导入期副作用：这一行不只是读 $UPLOAD_DIR 下的索引，它会在索引缺 owner 时
# **改写用户真实的数据文件**，并在旁边落下一个 index.json.bak-<时间戳>。
# $UPLOAD_DIR 未设置时解析到仓库真实的 data/uploads/，所以任何脚本、REPL、
# 测试都必须"先定 env，再 import app.core.uploads"（本任务的一次迁移演练正是
# 漏了这一步，把真实索引给迁了）。fail-fast 是刻意的：宁可启动失败，也不让
# 没有归属的记录被校验放行。改成惰性构造留给 Task 8，这一轮先付注释这笔钱。
store = UploadStore()


def build_user_content(text: str, attachment_ids: list, supports_vision: bool,
                       owner: str):
    """把附件拼进用户消息。

    文本/代码并入正文文本，这样写进会话历史后追问时上下文不丢；图片只在模型
    声明支持视觉时走多模态数组，否则明确报错——静默丢弃图片会让用户以为模型"看
    不懂"。

    owner 由调用方（路由）从凭据里取，绝不从附件 id 反查是谁传的：附件 id 是
    可以被人塞进请求的，谁能引用它不等于谁拥有它。
    """
    body = (text or "").strip()
    images = []

    for upload_id in attachment_ids or []:
        record = store.get(upload_id, owner)
        if record is None:
            raise UploadError(f"附件不存在或已清理：{upload_id}")
        if record["kind"] == "text":
            block = store.read_text(upload_id, owner=owner)
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
                      "image_url": {"url": store.data_uri(record["id"], owner=owner)}})
    return parts
