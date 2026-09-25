"""模型服务（Provider）配置。

界面展示的模型、下拉里能选什么、以及真正调用时用的 base_url/密钥/模型名，
全部来自这一份数据。此前它们分散在 main.py 的手写清单、llm_client.MODEL_CONFIGS
和 streaming.MODEL_CONFIG 三处，互不同步，导致"显示 DeepSeek 实际调 GPT"、
"选了没反应的模型"这类问题无法根治。
"""
import hashlib
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
        # replace 原子不等于落盘：flush + fsync 之后才 replace，断电不丢配置。
        f.flush()
        os.fsync(f.fileno())
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


# 模型档案里"上下文上限（K token）"的兜底值：没填的条目按 64K 算。
# 定成 64 而不是更大：它是"界面敢让你把预算拉到多远"的闸门，虚高的代价是
# 越界报错（上游 4xx），偏低的代价只是少带几轮历史——选边明显。
DEFAULT_MAX_CONTEXT_K = 64
MAX_CONTEXT_K_CEIL = 10000

def normalize_max_context_k(value) -> int:
    """把任意输入洗成 1..10000 的整数 K；None/空/非法 → 缺省 64。

    写路径（_validate）与读路径（catalog/_public 对老记录的兜底）共用这一个口径，
    别各写一份——两处数字对不上时，界面封顶和实际预算会静默分叉。
    """
    if value is None or value == "":
        return DEFAULT_MAX_CONTEXT_K
    try:
        k = int(float(value))
    except (TypeError, ValueError):
        return DEFAULT_MAX_CONTEXT_K
    return max(1, min(MAX_CONTEXT_K_CEIL, k))

# 常见 OpenAI 兼容端点，仅作为新增表单的预填模板，用户可改
PRESETS = {
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
                 "model": "deepseek-flash", "supports_vision": True,
                 # 官方 v4 系（flash/pro）窗口 1M token（2026-09 用户核实）
                 "max_context_k": 1000},
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
        "max_context_k": PRESETS["deepseek"]["max_context_k"],
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
        # 每人"默认用哪个模型"也是同一份配置的第三张脸，同样从记录路径推导。
        # 此前这个偏好只活在设备 localStorage 里，换手机就回到全局默认——
        # 用户在自己的私有模型和管理员共享模型之间的选择从此有了一份服务端答案。
        self.prefs_path = os.path.join(os.path.dirname(self.path), "provider_prefs.json")
        self._lock = threading.Lock()
        self._items = []
        self._keys = {}
        self._prefs = self._load_prefs()
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

    def _load_prefs(self):
        if not os.path.isfile(self.prefs_path):
            return {}
        try:
            with open(self.prefs_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (ValueError, OSError) as e:
            # 偏好读不动就当没设过：default_for 会回落全局默认，用户顶多回到
            # "用站级默认"，不该把整个聊天链路拖崩。
            print(f"⚠️ 模型偏好读不了（{e}），本轮按未设置默认处理")
            return {}

    def _write_prefs(self):
        _write_json_atomic(self.prefs_path, self._prefs)

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
        # 写路径 = 配置变了（改 key、换 base_url、动 timeout 之外的任何一维都算）。
        # 客户端缓存整表作废，下一次 build_client 用新值重建——漏掉这一句，
        # "改了设置没生效"会精确复现在上游连接层。
        invalidate_client_cache()


    # ---- 查询 ----
    def all(self) -> list:
        with self._lock:
            return [dict(p) for p in self._items]

    @staticmethod
    def _is_private(p: dict) -> bool:
        return bool(p.get("owner"))

    @staticmethod
    def _in_pool(p: dict, user_id: str) -> bool:
        """某个用户"看得见、用得了"的判定：全局共享条目人人可见，私有条目只有主人可见。"""
        owner = p.get("owner") or ""
        return not owner or (user_id is not None and owner == user_id)

    def pool(self, user_id: str = None) -> list:
        """该用户可用的 provider 全集（共享 + 自己的私有）。user_id 为 None 只看共享。"""
        with self._lock:
            return [dict(p) for p in self._items if self._in_pool(p, user_id)]

    def mine(self, user_id: str) -> list:
        with self._lock:
            return [dict(p) for p in self._items if p.get("owner") == user_id]

    def visible_to(self, provider_id: str, user_id: str = None) -> bool:
        p = self.get(provider_id)
        return p is not None and self._in_pool(p, user_id)

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

        私有条目（owner 非空）永不参选：用户自带 key 的模型只服务他一个人，
        它要是能顶掉站级默认，一个人的配置就改变了所有人的默认上游。
        """
        with self._lock:
            shared = [p for p in self._items if not self._is_private(p)]
            usable = [p for p in shared
                      if not looks_placeholder(p.get("api_key", ""))]
            for pool in (usable, shared):
                if not pool:
                    continue
                for p in pool:
                    if p.get("is_default"):
                        return dict(p)
                return dict(pool[0])
            return None

    def get_pref(self, user_id: str):
        if not user_id:
            return None
        with self._lock:
            return self._prefs.get(user_id)

    def default_for(self, user_id: str = None):
        """「这个人不指定模型时真正会用哪个」的唯一答案。

        顺序：他自己的默认偏好（仍在其可用池内且密钥可用）→ 站级默认。
        偏好失效（被删、密钥被清空）不报错，静默回落——换一台设备登录的人
        不该因为上一台设备选过什么而被挡住聊天。
        """
        pref_id = self.get_pref(user_id)
        if pref_id:
            for p in self.pool(user_id):
                if p["id"] == pref_id and not looks_placeholder(p.get("api_key", "")):
                    return p
        return self.default()

    def set_pref(self, user_id: str, provider_id: str) -> None:
        """把「我的默认模型」持久化到服务端。可指向共享或自己的私有条目。"""
        if not user_id:
            raise ProviderError("需要登录身份才能设置默认模型")
        provider = self.get(provider_id)
        if provider is None or not self._in_pool(provider, user_id):
            raise ProviderError("模型服务不存在")
        if looks_placeholder(provider.get("api_key", "")):
            raise ProviderError(f"模型「{provider.get('label')}」缺少有效密钥")
        with self._lock:
            self._prefs[user_id] = provider_id
            self._write_prefs()

    def resolve(self, provider_id: str = None, legacy_model: str = None,
                user_id: str = None) -> dict:
        """按 provider id 取配置；兼容旧的 model 字段；都没有则用默认。

        user_id 是给 HTTP 入口用的归属闸门：带了它，查找只在"共享 + 本人私有"
        这个池子里做，别人的私有条目和根本不存在的条目是同一种失败（回落默认），
        这条链路因此不会变成一个"这个 provider id 存在吗"的探测器。
        不带 user_id 的既有调用方（pipeline/streaming/agents）拿的是入口已验过
        归属的具体 id，行为保持原样。
        """
        wanted = provider_id or legacy_model
        pool = self.pool(user_id)
        provider = self.get(wanted) if wanted else None
        if provider is not None and user_id is not None and not self._in_pool(provider, user_id):
            provider = None
        if provider is None and wanted:
            # 旧客户端可能传 provider id 之外的写法（如模型名），忽略大小写在池内再试一次
            for p in pool:
                if p["id"].lower() == wanted.lower() or p.get("model", "").lower() == wanted.lower():
                    provider = p
                    break
        if provider is None:
            provider = self.default_for(user_id) if user_id is not None else self.default()
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
                # 私有记录永不占站级默认位（包括"库里第一条"的自动置顶）。
                if self._is_private(cleaned):
                    cleaned["is_default"] = False
                else:
                    cleaned["is_default"] = cleaned["is_default"] or not self._items
                self._items.append(cleaned)
                # 「最多一颗 ★」是不变式，两条写路径都得守：更新分支清了别人的，
                # 新增分支不清的话库里能存下两颗，而 default() 只认遍历到的第一颗。
                if cleaned["is_default"]:
                    self._clear_default_except(cleaned["id"])
            else:
                # 归属是服务端管的事实，不从请求里取：更新一条已存在的记录时
                # owner 一律沿用库内那份——管理员的 PUT 不该把共享条目"改姓"，
                # 也不该有任何路径能把私有条目转公或转给另一个人。
                cleaned["owner"] = self._items[existing].get("owner") or ""
                if self._is_private(cleaned):
                    cleaned["is_default"] = False
                # 未填新密钥时保留原密钥，避免编辑界面回显掩码后被写回
                if looks_placeholder(cleaned["api_key"]):
                    cleaned["api_key"] = self._items[existing].get("api_key", "")
                # 同理保留计费归属：前端 PUT 的请求模型（ProviderRequest）没有
                # paid_by 字段，_validate 一律兜底成 operator。不接回来的话，
                # "用户自带 key" 那条账每改一次配置就被静默翻成"管理员垫钱"，
                # 账本从此是假账。只有调用方明确传了 paid_by 才允许改。
                if not str(record.get("paid_by") or "").strip():
                    cleaned["paid_by"] = self._items[existing].get("paid_by") or "operator"
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
                first_shared = next((p for p in self._items if not self._is_private(p)), None)
                if first_shared is not None:
                    first_shared["is_default"] = True
            # 有人把默认押在这条上：删了就顺手清偏好，让 default_for 静默回落，
            # 而不是留一个指向幽灵 id 的偏好永远走回落分支（行为一样，但脏）。
            dirty = False
            for uid, pid in list(self._prefs.items()):
                if pid == provider_id:
                    del self._prefs[uid]
                    dirty = True
            if dirty:
                self._write_prefs()
            self._flush()
            return True

    def set_default(self, provider_id: str) -> bool:
        with self._lock:
            hit = next((p for p in self._items if p["id"] == provider_id), None)
            if hit is None or self._is_private(hit):
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
        # owner 只由服务端写路径设置（/v1/me/providers 钉上调用者 user_id）；
        # 这里只做归一，空串 = 全局共享。
        owner = str(record.get("owner") or "").strip()
        return {
            "id": str(record.get("id") or f"p_{uuid.uuid4().hex[:8]}"),
            "label": label,
            "base_url": base_url.rstrip("/"),
            "api_key": str(record.get("api_key") or "").strip(),
            "model": model,
            "supports_vision": bool(record.get("supports_vision")),
            "max_context_k": normalize_max_context_k(record.get("max_context_k")),
            "is_default": bool(record.get("is_default")),
            "paid_by": paid_by,
            "owner": owner,
        }

    # ---- 对外视图（绝不返回明文密钥）----
    def public_list(self, user_id: str = None) -> list:
        """user_id=None → 全局共享清单（管理员面）；带 user_id → 该用户可用池。"""
        return [self._public(p) for p in self.pool(user_id)]

    def mine_public(self, user_id: str) -> list:
        return [self._public(p) for p in self.mine(user_id)]

    def catalog(self, user_id: str = None) -> list:
        items = []
        pool = self.pool(user_id) if user_id is not None else [
            p for p in self.all() if not self._is_private(p)]
        for p in pool:
            usable = not looks_placeholder(p.get("api_key", ""))
            items.append({
                "id": p["id"],
                "name": p["label"],
                "model": p["model"],
                "supports_vision": p["supports_vision"],
                # 老记录可能在字段加入之前就躺在盘上：读路径同样过一次归一，
                # 界面封顶永远拿到一个真数字，而不是 undefined。
                "max_context_k": normalize_max_context_k(p.get("max_context_k")),
                "default": bool(p.get("is_default")),
                "shared": not self._is_private(p),
                "usable": usable,
                "reason": "" if usable else "未配置有效密钥",
            })
        return items

    @staticmethod
    def _public(p: dict) -> dict:
        return {
            "id": p["id"], "label": p["label"], "base_url": p["base_url"],
            "model": p["model"], "supports_vision": p["supports_vision"],
            "max_context_k": normalize_max_context_k(p.get("max_context_k")),
            "is_default": bool(p.get("is_default")),
            "shared": not ProviderStore._is_private(p),
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


def _client_cache_key(provider: dict, timeout: float, max_retries: int) -> tuple:
    """缓存键带全"会改变上游连接行为"的每一维。
    api_key 只进摘要不进字典键：键会随 dump/日志排查被整份打出来，
    明文凭据不该多活一份副本。
    """
    raw = f"{provider.get('id')}|{provider.get('base_url')}|{provider.get('api_key')}"
    return (hashlib.sha256(raw.encode("utf-8")).hexdigest(), timeout, max_retries)


def build_client(provider: dict, timeout: float = 120.0, max_retries: int = 2) -> OpenAI:
    """唯一一个构造上游客户端的地方。

    timeout/max_retries 以前是各调用点自己传的（聊天走默认、探活走 20s/0 次），
    于是"探活"自己又现构了一份客户端——那份和这份漂移出一个参数，
    就会出现"探活说通、聊天说超时"这种查不出形状的话。

    同一份配置现在只建一次（2026-09-23 审查 #9）：旧写法每次聊天都新建
    `httpx.Client` + `OpenAI` 且从不 close，没有连接复用、fd 一路泄漏。
    构造放锁内：客户端的构造不发网络请求，代价可忽略，换来的是"两个并发
    首调各建一份、后一份覆盖前一份、前一份永远没人 close"这种竞态不存在。
    任何配置写路径（_flush）整表作废：改过 key/base_url 的下一次调用必然建新的。
    """
    key = _client_cache_key(provider, timeout, max_retries)
    with _CLIENT_CACHE_LOCK:
        client = _CLIENT_CACHE.get(key)
        if client is None:
            client = OpenAI(api_key=provider["api_key"], base_url=provider["base_url"],
                            timeout=timeout, max_retries=max_retries,
                            http_client=httpx.Client(verify=system_ssl_context()))
            _CLIENT_CACHE[key] = client
        return client


def invalidate_client_cache() -> None:
    """配置变更时作废整表。不按键精确淘汰是刻意的：条目就几 providers 的量，
    整表清空没有代价，而"哪些维度算变更"以后一旦漂移，精确淘汰就会留下旧客户端。"""
    with _CLIENT_CACHE_LOCK:
        _CLIENT_CACHE.clear()


_CLIENT_CACHE = {}
_CLIENT_CACHE_LOCK = threading.Lock()


store = ProviderStore()
