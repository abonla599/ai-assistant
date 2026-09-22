"""模型服务（Provider）配置。

界面展示的模型、下拉里能选什么、以及真正调用时用的 base_url/密钥/模型名，
全部来自这一份数据。此前它们分散在 main.py 的手写清单、llm_client.MODEL_CONFIGS
和 streaming.MODEL_CONFIG 三处，互不同步，导致"显示 DeepSeek 实际调 GPT"、
"选了没反应的模型"这类问题无法根治。
"""
import json
import os
import re
import threading
import uuid

import httpx
from openai import OpenAI

from app.core.paths import data_root, load_project_env
from app.core.tls import system_ssl_context

# 本模块可能早于其他组件被导入（app.pipeline 就会），因此自行加载 .env，
# 否则首次运行时读不到 DEEPSEEK_API_KEY、播种不出任何模型配置。
load_project_env()

PLACEHOLDER_HINTS = ("your-", "your_", "xxx", "placeholder", "填入", "待填", "changeme")

# 出口脱敏要认的形状。宁可多隐一些字，也不要漏一条 key：这些字符串同时会进
# data/backend.log 和管理员看得到的响应体，而日志文件不在任何加密范围内。
_SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|pk|key|api|secret|token|auth)[-_][A-Za-z0-9_.\-]{5,}", re.I),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/\-]+"),
    re.compile(r"(?i)(api[_-]?key|authorization|access[_-]?token|secret)[\"']?\s*[:=]\s*[\"']?([^\s\"',;]{5,})"),
)
REDACTED = "«密钥已隐去»"


def _write_json_atomic(path: str, payload) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _own_key_values():
    """服务端自己配过哪些密钥值——形状认不出来时唯一还认得出来的东西。

    读的是进程级那份 store；构造期它还没建好，所以取不到就当没有。
    短于 8 位的不参与：那种值出现在正常报错文案里的概率比它是 key 的概率高，
    隐去它只会让日志读不出所以然。
    """
    try:
        items = store.all()
    except NameError:
        return []
    return sorted({p.get("api_key", "").strip() for p in items
                   if len(p.get("api_key", "").strip()) >= 8}, key=len, reverse=True)


def scrub_secrets(text: str) -> str:
    """把一句要往外说的话里的凭据形状与已配置的密钥值都隐掉。

    先按值替换再按形状替换：值是唯一确定的，形状是猜的——猜的那一层不该把
    已经确认过的东西留下空隙。
    """
    if not text:
        return text
    out = str(text)
    for value in _own_key_values():
        out = out.replace(value, REDACTED)
    for pattern in _SECRET_PATTERNS:
        # 第三条有两组：留下"api_key="这个标签，隐掉冒号后面那串。
        # 写成 group(1) + group(2) 就等于把"隐去"实现成了"原样保留"。
        out = pattern.sub(lambda m: (m.group(1) + REDACTED) if m.lastindex == 2
                          else REDACTED, out)
    return out



class ProviderError(Exception):
    """配置缺失或模型不可用。上层据此返回明确错误，而不是把故障当成回复内容。"""


def _default_path() -> str:
    env_path = os.getenv("PROVIDERS_DB_PATH")
    if env_path:
        return os.path.abspath(env_path)
    return os.path.join(data_root(), "data", "providers.json")


def mask_key(key: str) -> str:
    """掩码是给前端的**唯一**凭据线索，所以它自己不能变成第二个泄露点。

    旧写法把前 3 位与总长度一起给出去（`sk-…3839（35 位）`）：前缀能认出厂商，
    长度能框定爆破面，而"管理员在同一个页面上看得懂哪一把"只需要末 4 位。
    """
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return f"末四位 {key[-4:]}"



def looks_placeholder(key: str) -> bool:
    """非空但形如占位符的密钥，此前能骗过 `if not api_key` 闸门，是"切换模型没反应"的根因。"""
    if not key or not key.strip():
        return True
    lowered = key.strip().lower()
    return any(h in lowered for h in PLACEHOLDER_HINTS)


# 常见 OpenAI 兼容端点，仅作为新增表单的预填模板，用户可改
PRESETS = {
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
                 "model": "deepseek-flash", "supports_vision": True},
    "dashscope": {"label": "阿里云百炼 Qwen", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus", "supports_vision": False},
    "dashscope-vl": {"label": "阿里云百炼 Qwen 视觉", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-vl-max", "supports_vision": True},
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4o", "supports_vision": True},
}


def _seed_from_env() -> list:
    """首次运行时用 .env 里已有的密钥建一条 DeepSeek，避免升级后无模型可用。

    这里也是 DEEPSEEK_BASE_URL 第一次真正被代码读取的地方——此前它写在 .env 里
    却没有任何代码引用，用户改了也不生效。
    """
    key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
    if looks_placeholder(key):
        return []
    # deepseek-flash 是 api.deepseek.com 现在列出的、实测能吃图的那一个
    # （2026-09-20 用真截图验过：prompt_tokens 计入图像、能读出图中文字）；
    # 同端点的 deepseek-v4-pro 会直接回"我无法查看这张图片"，别拿它当视觉模型。
    return [{
        "id": "deepseek-chat",
        "label": "deepseek-flash",
        "base_url": (os.getenv("DEEPSEEK_BASE_URL") or PRESETS["deepseek"]["base_url"]).strip(),
        "api_key": key,
        "model": "deepseek-flash",
        "supports_vision": True,
        "is_default": True,
    }]


class ProviderStore:
    def __init__(self, path: str = None):
        self.path = os.path.abspath(path or _default_path())
        # 密钥单独一个文件，路径从记录文件推导而不是再开一个环境变量：
        # 两处事实来源迟早会漂移（"搬了记录没搬密钥"就是下一次的数据丢失），
        # 而 PROVIDERS_DB_PATH 一个变量本来就该把这份配置整体指走。
        self.keys_path = os.path.join(os.path.dirname(self.path), "provider_keys.json")
        self._lock = threading.Lock()
        self._items = []
        self._keys = {}
        self._load()

    def _load_keys(self):
        if not os.path.isfile(self.keys_path):
            return {}
        try:
            with open(self.keys_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (ValueError, OSError) as e:
            # 读不动就当没有：宁可让 resolve() 说「缺少有效密钥」，
            # 也不要在这里悄悄把内联在记录里的 key 复制回盘上——那等于没搬。
            print(f"⚠️ 密钥文件读不了（{e}），本轮按未配置密钥处理")
            return {}

    def _write_keys(self):
        _write_json_atomic(self.keys_path, self._keys)

    def _load(self):
        migrated = False
        if os.path.isfile(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self._items = [p for p in data if isinstance(p, dict) and p.get("id")]
                    self._keys = self._load_keys()
                    for p in self._items:
                        inline = p.pop("api_key", "") or ""
                        if inline:
                            # 升级前那份文件里 key 就写在记录上；读的时候顺手搬走
                            self._keys.setdefault(p["id"], inline)
                            migrated = True
                        p["api_key"] = self._keys.get(p["id"], "")
                    if migrated:
                        self._flush()
                    self._register_log_terms()
                    return
            except (ValueError, OSError) as e:
                backup = self.path + ".corrupt"
                try:
                    os.replace(self.path, backup)
                    print(f"⚠️ Provider 配置损坏（{e}），已备份为 {backup}")
                except OSError:
                    print(f"⚠️ Provider 配置损坏且无法备份（{e}）")
        self._keys = self._load_keys()
        self._items = _seed_from_env()
        if self._items:
            self._flush()
            print(f"ℹ️ 已从 .env 初始化 {len(self._items)} 个模型服务配置")

    def _register_log_terms(self):
        # 服务商名/模型 id/上游 host 一旦进过配置，就不许再明文进日志：登记进
        # logsanitizer，之后所有落盘输出统一打码。判据见 app/core/logsanitizer.py。
        from urllib.parse import urlparse
        from app.core import logsanitizer
        for p in self._items:
            logsanitizer.register(p.get("model", ""))
            logsanitizer.register(p.get("label", ""))
            host = urlparse(p.get("base_url", "") or "").hostname
            if host:
                logsanitizer.register(host)

    def _flush(self):
        # 调用方都持着 self._lock（_load 在构造期单线程）。写记录时把 api_key 整个剔掉，
        # 留空字段比删字段更糟：下一个读这份文件的人会以为值在这儿。
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        _write_json_atomic(self.path, [{k: v for k, v in p.items() if k != "api_key"}
                                       for p in self._items])
        self._keys = {p["id"]: p.get("api_key", "") for p in self._items if p.get("api_key")}
        _write_json_atomic(self.keys_path, self._keys)
        self._register_log_terms()


    # ---- 查询 ----
    def all(self) -> list:
        with self._lock:
            return [dict(p) for p in self._items]

    def get(self, provider_id: str):
        with self._lock:
            for p in self._items:
                if p["id"] == provider_id:
                    return dict(p)
        return None

    def default(self):
        """「调用方没给 id 时真正会用哪个」的唯一答案（/v1/models 的 default 就是它）。

        只在**可用**的里面挑，判据与 catalog() 给前端的 usable 同一条
        （looks_placeholder）：此前这里只看 is_default 标记，而前端是
        `usable.find(p => p.default) || usable[0]`，于是管理员把 ★ 点在一条只填了
        占位符密钥的配置上时，界面显示的是 B、实际发请求用的是 A——"默认是哪个"
        有了两个答案。现在前端不再自己挑（见 static/app.js 的 serverDefaultProvider）。

        一个都不可用时仍退回老顺序，好让 resolve() 说出准确那句「缺少有效密钥」，
        而不是把"配了但没填 key"说成"尚未配置任何模型服务"。
        """
        with self._lock:
            usable = [p for p in self._items
                      if not looks_placeholder(p.get("api_key", ""))]
            for pool in (usable, self._items):
                if not pool:
                    continue
                for p in pool:
                    if p.get("is_default"):
                        return dict(p)
                return dict(pool[0])
            return None

    def resolve(self, provider_id: str = None, legacy_model: str = None) -> dict:
        """按 provider id 取配置；兼容旧的 model 字段；都没有则用默认。"""
        wanted = provider_id or legacy_model
        provider = self.get(wanted) if wanted else None
        if provider is None and wanted:
            # 旧客户端可能传 "deepseek-chat" 之外的写法，忽略大小写再试一次
            for p in self.all():
                if p["id"].lower() == wanted.lower() or p.get("model", "").lower() == wanted.lower():
                    provider = p
                    break
        if provider is None:
            provider = self.default()
        if provider is None:
            raise ProviderError("尚未配置任何模型服务，请在「设置 → 模型服务」中添加")
        if looks_placeholder(provider.get("api_key", "")):
            raise ProviderError(f"模型「{provider.get('label')}」缺少有效密钥，请在设置中填写 API Key")
        return provider

    # ---- 变更 ----
    def upsert(self, record: dict) -> dict:
        cleaned = self._validate(record)
        with self._lock:
            existing = next((i for i, p in enumerate(self._items) if p["id"] == cleaned["id"]), None)
            if existing is None:
                cleaned["is_default"] = cleaned["is_default"] or not self._items
                self._items.append(cleaned)
                # 「最多一颗 ★」是不变式，两条写路径都得守：更新分支清了别人的，
                # 新增分支不清的话库里能存下两颗，而 default() 只认遍历到的第一颗。
                if cleaned["is_default"]:
                    self._clear_default_except(cleaned["id"])
            else:
                # 未填新密钥时保留原密钥，避免编辑界面回显掩码后被写回
                if looks_placeholder(cleaned["api_key"]):
                    cleaned["api_key"] = self._items[existing].get("api_key", "")
                self._items[existing] = cleaned
                if cleaned["is_default"]:
                    self._clear_default_except(cleaned["id"])
            self._flush()
        return cleaned

    def delete(self, provider_id: str) -> bool:
        with self._lock:
            before = len(self._items)
            self._items = [p for p in self._items if p["id"] != provider_id]
            if len(self._items) == before:
                return False
            if self._items and not any(p.get("is_default") for p in self._items):
                self._items[0]["is_default"] = True
            self._flush()
            return True

    def set_default(self, provider_id: str) -> bool:
        with self._lock:
            if not any(p["id"] == provider_id for p in self._items):
                return False
            self._clear_default_except(provider_id)
            self._flush()
            return True

    def _clear_default_except(self, keep_id: str):
        for p in self._items:
            p["is_default"] = (p["id"] == keep_id)

    @staticmethod
    def _validate(record: dict) -> dict:
        label = str(record.get("label") or "").strip()
        base_url = str(record.get("base_url") or "").strip()
        model = str(record.get("model") or "").strip()
        if not label:
            raise ProviderError("label 不能为空")
        if not base_url.startswith(("http://", "https://")):
            raise ProviderError("base_url 必须以 http:// 或 https:// 开头")
        if not model:
            raise ProviderError("model 不能为空")
        # 「这口钱谁出」是账本上的一列，不是可选备注：内置=operator、用户自带=user。
        # 缺省给 operator，因为现存那几条记录确实都是管理员垫的——把缺省定成 user
        # 会让升级后的第一天账目集体变成"用户自己的钱"，那是假账。
        paid_by = str(record.get("paid_by") or "operator").strip()
        if paid_by not in ("operator", "user"):
            raise ProviderError("paid_by 只许 operator 或 user")
        return {
            "id": str(record.get("id") or f"p_{uuid.uuid4().hex[:8]}"),
            "label": label,
            "base_url": base_url.rstrip("/"),
            "api_key": str(record.get("api_key") or "").strip(),
            "model": model,
            "supports_vision": bool(record.get("supports_vision")),
            "is_default": bool(record.get("is_default")),
            "paid_by": paid_by,
        }

    # ---- 对外视图（绝不返回明文密钥）----
    def public_list(self) -> list:
        return [self._public(p) for p in self.all()]

    def catalog(self) -> list:
        items = []
        for p in self.all():
            usable = not looks_placeholder(p.get("api_key", ""))
            items.append({
                "id": p["id"],
                "name": p["label"],
                "model": p["model"],
                "supports_vision": p["supports_vision"],
                "default": bool(p.get("is_default")),
                "usable": usable,
                "reason": "" if usable else "未配置有效密钥",
            })
        return items

    @staticmethod
    def _public(p: dict) -> dict:
        return {
            "id": p["id"], "label": p["label"], "base_url": p["base_url"],
            "model": p["model"], "supports_vision": p["supports_vision"],
            "is_default": bool(p.get("is_default")),
            "api_key_masked": mask_key(p.get("api_key", "")),
            "has_key": not looks_placeholder(p.get("api_key", "")),
            "paid_by": p.get("paid_by") or "operator",
        }

    # ---- 探活 ----
    def ping(self, provider_id: str) -> dict:
        provider = self.resolve(provider_id)
        try:
            client = build_client(provider, timeout=20.0, max_retries=0)
            completion = client.chat.completions.create(
                model=provider["model"],
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=4,
            )
            return {"ok": True, "detail": f"{provider['model']} 响应正常",
                    "sample": (completion.choices[0].message.content or "")[:40]}
        except Exception as e:
            # 这一句会进管理员的屏幕。上游/中转站把 Authorization 原样打印回来
            # 不是假设，是这类网关的常见做法，所以出口在这儿过一次。
            return {"ok": False, "detail": scrub_secrets(f"{type(e).__name__}: {str(e)[:180]}")}


def build_client(provider: dict, timeout: float = 120.0, max_retries: int = 2) -> OpenAI:
    """唯一一个构造上游客户端的地方。

    timeout/max_retries 以前是各调用点自己传的（聊天走默认、探活走 20s/0 次），
    于是"探活"自己又现构了一份客户端——那份和这份漂移出一个参数，
    就会出现"探活说通、聊天说超时"这种查不出形状的话。
    """
    return OpenAI(api_key=provider["api_key"], base_url=provider["base_url"],
                  timeout=timeout, max_retries=max_retries,
                  http_client=httpx.Client(verify=system_ssl_context()))


store = ProviderStore()
