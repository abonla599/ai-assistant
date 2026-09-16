"""模型服务（Provider）配置。

界面展示的模型、下拉里能选什么、以及真正调用时用的 base_url/密钥/模型名，
全部来自这一份数据。此前它们分散在 main.py 的手写清单、llm_client.MODEL_CONFIGS
和 streaming.MODEL_CONFIG 三处，互不同步，导致"显示 DeepSeek 实际调 GPT"、
"选了没反应的模型"这类问题无法根治。
"""
import json
import os
import re
import sys
import threading
import uuid

from openai import OpenAI
from dotenv import load_dotenv

# 本模块可能早于其他组件被导入（app.pipeline 就会），因此自行加载 .env，
# 否则首次运行时读不到 DEEPSEEK_API_KEY、播种不出任何模型配置。
load_dotenv()

PLACEHOLDER_HINTS = ("your-", "your_", "xxx", "placeholder", "填入", "待填", "changeme")


class ProviderError(Exception):
    """配置缺失或模型不可用。上层据此返回明确错误，而不是把故障当成回复内容。"""


def _default_path() -> str:
    env_path = os.getenv("PROVIDERS_DB_PATH")
    if env_path:
        return os.path.abspath(env_path)
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
    return os.path.join(base, "data", "providers.json")


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return f"{key[:3]}…{key[-4:]}（{len(key)} 位）"


def looks_placeholder(key: str) -> bool:
    """非空但形如占位符的密钥，此前能骗过 `if not api_key` 闸门，是"切换模型没反应"的根因。"""
    if not key or not key.strip():
        return True
    lowered = key.strip().lower()
    return any(h in lowered for h in PLACEHOLDER_HINTS)


# 常见 OpenAI 兼容端点，仅作为新增表单的预填模板，用户可改
PRESETS = {
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "supports_vision": False},
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
    return [{
        "id": "deepseek-chat",
        "label": "DeepSeek Chat",
        "base_url": (os.getenv("DEEPSEEK_BASE_URL") or PRESETS["deepseek"]["base_url"]).strip(),
        "api_key": key,
        "model": "deepseek-chat",
        "supports_vision": False,
        "is_default": True,
    }]


class ProviderStore:
    def __init__(self, path: str = None):
        self.path = os.path.abspath(path or _default_path())
        self._lock = threading.Lock()
        self._items = []
        self._load()

    def _load(self):
        if os.path.isfile(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self._items = [p for p in data if isinstance(p, dict) and p.get("id")]
                    return
            except (ValueError, OSError) as e:
                backup = self.path + ".corrupt"
                try:
                    os.replace(self.path, backup)
                    print(f"⚠️ Provider 配置损坏（{e}），已备份为 {backup}")
                except OSError:
                    print(f"⚠️ Provider 配置损坏且无法备份（{e}）")
        self._items = _seed_from_env()
        if self._items:
            self._flush()
            print(f"ℹ️ 已从 .env 初始化 {len(self._items)} 个模型服务配置")

    def _flush(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._items, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

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
        with self._lock:
            for p in self._items:
                if p.get("is_default"):
                    return dict(p)
            return dict(self._items[0]) if self._items else None

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
        return {
            "id": str(record.get("id") or f"p_{uuid.uuid4().hex[:8]}"),
            "label": label,
            "base_url": base_url.rstrip("/"),
            "api_key": str(record.get("api_key") or "").strip(),
            "model": model,
            "supports_vision": bool(record.get("supports_vision")),
            "is_default": bool(record.get("is_default")),
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
        }

    # ---- 探活 ----
    def ping(self, provider_id: str) -> dict:
        provider = self.resolve(provider_id)
        try:
            client = OpenAI(api_key=provider["api_key"], base_url=provider["base_url"],
                            timeout=20.0, max_retries=0)
            completion = client.chat.completions.create(
                model=provider["model"],
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=4,
            )
            return {"ok": True, "detail": f"{provider['model']} 响应正常",
                    "sample": (completion.choices[0].message.content or "")[:40]}
        except Exception as e:
            return {"ok": False, "detail": f"{type(e).__name__}: {str(e)[:180]}"}


def build_client(provider: dict) -> OpenAI:
    return OpenAI(api_key=provider["api_key"], base_url=provider["base_url"])


store = ProviderStore()
