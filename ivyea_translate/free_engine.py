"""内置免费翻译引擎：无需任何 Key，开箱即用。

内置四个公开端点，按质量排序并带熔断回退（不同网络环境各有可用的）：
  1. DeepL（keyless jsonrpc）——质量最好，但对数据中心 IP 限流较凶（首个请求常成功，
     随后 429）。故作"有则更好"的首选，失败即进入冷却，不拖慢后续翻译。
  2. Google gtx（translate.googleapis.com）——无 token，纯 GET，最稳；国内直连不通。
  3. 腾讯交互翻译 TranSmart（transmart.qq.com）——国内直连、质量好、支持全部目标语言。
  4. 必应网页接口（bing.com）——2026-09 实测对本机 IP 返回 401，留作最后兜底。

对冲发请求（v0.35.0）：曾经是严格串行——首选端点被墙就要先等满 6 秒超时才轮到
下一个，用户只看到"翻译中"转圈。现在首选先发，HEDGE_STAGGER_S 内没回来就并行
再发下一个，谁先成功用谁，其余结果丢弃。DeepL 限流凶，只当首发、绝不当对冲补位。

FreeEngine 记住上次成功端点优先用；失败端点进入冷却期排到最后；全部冷却时仍兜底重试。
免费引擎不支持"风格"（美式/正式等），风格仅大模型引擎生效。
"""
from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Callable, Dict, List, Optional, Tuple

import httpx

from .llm import LLMError

log = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

MAX_CHUNK = 900          # 单次请求字符上限
COOLDOWN_S = 600         # 端点失败后的冷却时长（秒）
TIMEOUT_S = 6.0          # 通用端点超时：15s 太久——被墙的端点（国内 Google）会把
                         # 整条回退链拖成半分钟，用户只看到"翻译中"卡死
HEDGE_STAGGER_S = 0.35   # 首选端点这么久没回来就并行补发下一个（热连接一般 50-300ms 返回）
NO_HEDGE = {"deepl"}     # 只当首发、不当对冲补位的端点（限流凶，多发一次就是多一次 429）

# 应用语言码 -> 各服务语言码
DEEPL_LANG: Dict[str, str] = {
    "zh-CN": "ZH", "en": "EN", "ja": "JA", "ko": "KO", "fr": "FR",
    "de": "DE", "es": "ES", "ru": "RU", "pt": "PT", "it": "IT",
    # DeepL 不支持 zh-TW/ar/vi/th 或支持不稳，留给 Google/Bing
}
GOOGLE_LANG: Dict[str, str] = {
    "zh-CN": "zh-CN", "zh-TW": "zh-TW", "en": "en", "ja": "ja", "ko": "ko",
    "fr": "fr", "de": "de", "es": "es", "ru": "ru", "pt": "pt", "it": "it",
    "ar": "ar", "vi": "vi", "th": "th",
}
# TranSmart 不支持繁体目标（传 zh-TW 会静默给简体），留给 Google/Bing
TRANSMART_LANG: Dict[str, str] = {
    "zh-CN": "zh", "en": "en", "ja": "ja", "ko": "ko", "fr": "fr", "de": "de",
    "es": "es", "ru": "ru", "pt": "pt", "it": "it", "ar": "ar", "vi": "vi", "th": "th",
}
BING_LANG: Dict[str, str] = {
    "zh-CN": "zh-Hans", "zh-TW": "zh-Hant", "en": "en", "ja": "ja", "ko": "ko",
    "fr": "fr", "de": "de", "es": "es", "ru": "ru", "pt": "pt", "it": "it",
    "ar": "ar", "vi": "vi", "th": "th",
}


def split_for_translate(text: str, max_chunk: int = MAX_CHUNK) -> List[str]:
    """按段落切块，每块不超过 max_chunk；超长单段落再按字符硬切。纯函数。"""
    if len(text) <= max_chunk:
        return [text]
    chunks: List[str] = []
    buf = ""
    for para in text.split("\n"):
        candidate = (buf + "\n" + para) if buf else para
        if len(candidate) <= max_chunk:
            buf = candidate
            continue
        if buf:
            chunks.append(buf)
            buf = ""
        while len(para) > max_chunk:
            chunks.append(para[:max_chunk])
            para = para[max_chunk:]
        buf = para
    if buf:
        chunks.append(buf)
    return chunks


def _client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT_S, follow_redirects=True, headers={"User-Agent": _UA})


# 持久连接（keep-alive）：每次翻译新建 Client = 每次都重走 TCP+TLS 握手，
# 对境外端点 100-400ms，短文本场景握手比翻译本身还贵。httpx.Client 线程安全。
_client_lock = threading.Lock()
_shared: Optional[httpx.Client] = None      # Google 等通用端点
_deepl_cli: Optional[httpx.Client] = None   # DeepL 单独超时 4s


def _shared_client() -> httpx.Client:
    global _shared
    with _client_lock:
        if _shared is None or _shared.is_closed:
            _shared = _client()
        return _shared


def _deepl_client() -> httpx.Client:
    global _deepl_cli
    with _client_lock:
        if _deepl_cli is None or _deepl_cli.is_closed:
            _deepl_cli = httpx.Client(timeout=4.0, headers={"User-Agent": _UA})
        return _deepl_cli


_PREWARM_URLS = {
    "deepl": "https://www2.deepl.com",
    "google": "https://translate.googleapis.com",
    "transmart": "https://transmart.qq.com",
    "bing": "https://www.bing.com",
}
_PREWARM_MIN_GAP_S = 20.0
_last_prewarm = 0.0


def prewarm_async(force: bool = False) -> None:
    """后台预热 preferred 端点的 TLS 连接：翻译时不付握手税。绝不抛。

    启动时预热一次不够：服务端几分钟就掐掉空闲连接，用户下一次截图又付一次
    850ms 握手（本机实测 Google 冷 854ms / 热 52ms）。所以热键按下（用户还在框选）
    时再预热一次，20 秒内重复调用直接忽略。
    """
    global _last_prewarm
    now = time.monotonic()
    if not force and now - _last_prewarm < _PREWARM_MIN_GAP_S:
        return
    _last_prewarm = now

    def run():
        name = free_engine.preferred or "google"
        url = _PREWARM_URLS.get(name, _PREWARM_URLS["google"])
        try:
            client = _deepl_client() if name == "deepl" else _shared_client()
            client.get(url, timeout=4.0)
            log.info("免费引擎预热完成：%s", name)
        except Exception as e:
            log.info("免费引擎预热失败（不影响使用）：%s", e)

    threading.Thread(target=run, daemon=True).start()


# ---------- DeepL（keyless jsonrpc，best-effort） ----------

def _deepl_translate(text: str, target_language: str) -> str:
    to_lang = DEEPL_LANG.get(target_language)
    if not to_lang:
        raise LLMError(f"DeepL 不支持目标语言 {target_language}")
    c = _deepl_client()
    # DeepL 限流凶，分块保持串行，别并发触发 429
    out = [_deepl_chunk(c, chunk, to_lang) for chunk in split_for_translate(text)]
    return "\n".join(out) if len(out) > 1 else out[0]


def _deepl_chunk(client: httpx.Client, text: str, to_lang: str) -> str:
    ts = int(time.time() * 1000)
    i_count = text.count("i") + 1
    ts = ts - (ts % i_count) + i_count  # 模仿浏览器：时间戳整除 i 计数
    rid = random.randint(1_000_000, 9_999_999) * 1000
    payload = {
        "jsonrpc": "2.0", "method": "LMT_handle_texts", "id": rid,
        "params": {
            "texts": [{"text": text, "requestAlternatives": 0}],
            "splitting": "newlines",
            "lang": {"source_lang_user_selected": "auto", "target_lang": to_lang},
            "timestamp": ts,
            "commonJobParams": {"wasSpoken": False, "transcribe_as": ""},
        },
    }
    body = json.dumps(payload, ensure_ascii=False)
    spacing = '"method" : "' if ((rid + 5) % 29 == 0 or (rid + 3) % 13 == 0) else '"method": "'
    body = body.replace('"method":"', spacing, 1)
    resp = client.post(
        "https://www2.deepl.com/jsonrpc",
        content=body.encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Referer": "https://www.deepl.com/", "Origin": "https://www.deepl.com"},
    )
    if resp.status_code == 429:
        raise LLMError("DeepL 限流")
    resp.raise_for_status()
    data = resp.json()
    if "result" not in data:
        raise LLMError("DeepL 返回异常")
    return "".join(t["text"] for t in data["result"]["texts"])


# ---------- Google gtx ----------

def _google_translate(text: str, target_language: str) -> str:
    to_lang = GOOGLE_LANG.get(target_language, target_language)
    c = _shared_client()
    chunks = split_for_translate(text)
    if len(chunks) == 1:
        return _google_chunk(c, chunks[0], to_lang)
    # 块间无依赖：并行发省掉串行往返（并发 2 保守，Google gtx 扛得住）
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as ex:
        out = list(ex.map(lambda ch: _google_chunk(c, ch, to_lang), chunks))
    return "\n".join(out)


def _google_chunk(client: httpx.Client, text: str, to_lang: str) -> str:
    resp = client.get(
        "https://translate.googleapis.com/translate_a/single",
        params={"client": "gtx", "sl": "auto", "tl": to_lang, "dt": "t", "q": text},
    )
    resp.raise_for_status()
    data = resp.json()
    if not data or not data[0]:
        return ""
    return "".join(seg[0] for seg in data[0] if seg and seg[0])


# ---------- 腾讯交互翻译 TranSmart ----------

_TRANSMART_URL = "https://transmart.qq.com/api/imt"
_transmart_client_key = (
    f"browser-chrome-126.0.0-Windows 10-{uuid.uuid4()}-{int(time.time() * 1000)}")


def _transmart_translate(text: str, target_language: str) -> str:
    to_lang = TRANSMART_LANG.get(target_language)
    if not to_lang:
        raise LLMError(f"TranSmart 不支持目标语言 {target_language}")
    c = _shared_client()
    chunks = split_for_translate(text)
    if len(chunks) == 1:
        return _transmart_chunk(c, chunks[0], to_lang)
    from concurrent.futures import ThreadPoolExecutor as _Pool

    with _Pool(max_workers=2) as ex:
        out = list(ex.map(lambda ch: _transmart_chunk(c, ch, to_lang), chunks))
    return "\n".join(out)


def _transmart_chunk(client: httpx.Client, text: str, to_lang: str) -> str:
    # 按行送 text_list：服务端逐项返回，空行原样保留，段落结构不丢
    lines = text.split("\n")
    payload = {
        "header": {"fn": "auto_translation", "client_key": _transmart_client_key},
        "type": "plain",
        "model_category": "normal",
        "source": {"lang": "auto", "text_list": lines},
        "target": {"lang": to_lang},
    }
    resp = client.post(
        _TRANSMART_URL, json=payload,
        headers={"Referer": "https://transmart.qq.com/", "Origin": "https://transmart.qq.com"},
    )
    resp.raise_for_status()
    data = resp.json()
    header = data.get("header") or {}
    if header.get("ret_code") != "succ":
        raise LLMError(f"TranSmart 返回异常：{data.get('message') or header.get('ret_code')}")
    out = data.get("auto_translation") or []
    if len(out) != len(lines):
        raise LLMError("TranSmart 返回段数不匹配")
    return "\n".join(out)


# ---------- 必应 ----------

class _BingSession:
    def __init__(self):
        self.client: Optional[httpx.Client] = None
        self.ig = self.iid = self.key = self.token = None
        self.expire_at = 0.0
        self.iid_seq = 0
        # 会话状态（token/iid_seq/共享 client）非线程安全；弹窗+原位可能并发翻译
        self.lock = threading.Lock()

    def http(self) -> httpx.Client:
        if self.client is None:
            self.client = _client()
        return self.client

    def ensure(self) -> None:
        if self.token and time.time() < self.expire_at:
            return
        resp = self.http().get("https://www.bing.com/translator")
        resp.raise_for_status()
        html = resp.text
        m_ig = re.search(r'IG:"([A-Za-z0-9]+)"', html)
        m_iid = re.search(r'data-iid="([^"]+)"', html)
        m_helper = re.search(
            r'params_AbusePreventionHelper\s*=\s*\[\s*(\d+)\s*,\s*"([^"]+)"\s*,\s*(\d+)\s*\]', html)
        if not (m_ig and m_helper):
            raise LLMError("必应握手失败")
        self.ig = m_ig.group(1)
        self.iid = m_iid.group(1) if m_iid else "translator.5028"
        self.key = m_helper.group(1)
        self.token = m_helper.group(2)
        self.expire_at = time.time() + int(m_helper.group(3)) / 1000 - 60

    def chunk(self, text: str, to_lang: str) -> str:
        with self.lock:
            return self._chunk_locked(text, to_lang)

    def _chunk_locked(self, text: str, to_lang: str) -> str:
        self.ensure()
        self.iid_seq += 1
        url = f"https://www.bing.com/ttranslatev3?isVertical=1&IG={self.ig}&IID={self.iid}.{self.iid_seq}"
        resp = self.http().post(
            url,
            headers={"Referer": "https://www.bing.com/translator"},
            data={"fromLang": "auto-detect", "text": text, "to": to_lang,
                  "token": self.token, "key": self.key},
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and data.get("statusCode"):
            self.token = None
            raise LLMError("必应票据失效")
        return data[0]["translations"][0]["text"]


_bing = _BingSession()


def _bing_translate(text: str, target_language: str) -> str:
    to_lang = BING_LANG.get(target_language, target_language)
    out = []
    for chunk in split_for_translate(text):
        try:
            out.append(_bing.chunk(chunk, to_lang))
        except (httpx.HTTPStatusError, LLMError):
            _bing.token = None
            out.append(_bing.chunk(chunk, to_lang))  # 刷会话重试一次
    return "\n".join(out) if len(out) > 1 else out[0]


# ---------- 组合引擎（质量排序 + 熔断回退） ----------

_ENGINES: List[Tuple[str, Callable[[str, str], str]]] = [
    ("deepl", _deepl_translate),
    ("google", _google_translate),
    ("transmart", _transmart_translate),
    ("bing", _bing_translate),
]

# 对冲请求的执行池：一次翻译最多同时挂 2-3 个端点，弹窗+原位并发也够用。
# 被放弃的请求会在这里跑到超时为止（httpx 同步请求没法中途掐），不占主线程。
_hedge_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="free-hedge")


class FreeEngine:
    """多端点自动回退。首选上次成功端点；失败端点冷却期内跳过。

    锁只护 preferred/cooldown 状态，**绝不罩网络请求**（v0.26.2 教训）：曾用一把
    全局锁罩住整条回退链，一个被墙端点卡在超时里就攥锁半分钟，弹窗被点关后
    僵尸请求还抱着锁，用户后续所有翻译排在锁后面干等——表现为"翻译中"卡死、
    结果在旧请求超时的瞬间突然蹦出。
    """

    is_free = True

    def __init__(self):
        self._state_lock = threading.Lock()
        self._preferred: Optional[str] = None
        self._cooldown: Dict[str, float] = {}  # name -> 冷却结束时间戳

    @property
    def preferred(self) -> Optional[str]:
        return self._preferred

    @preferred.setter
    def preferred(self, value: Optional[str]) -> None:
        # 只接受已知端点名
        self._preferred = value if value in {n for n, _ in _ENGINES} else None

    def _order(self, now: float) -> List[Tuple[str, Callable]]:
        # 冷却中的端点排到最后（而非彻底剔除），保证全部冷却时仍有兜底
        def keyfn(item):
            name = item[0]
            cooling = self._cooldown.get(name, 0) > now
            return (cooling, name != self._preferred)
        return sorted(_ENGINES, key=keyfn)

    def translate(self, text: str, target_language: str,
                  should_abort: Optional[Callable[[], bool]] = None,
                  stagger_s: float = HEDGE_STAGGER_S) -> str:
        """翻译；首选端点先发，stagger_s 内没回来就并行补发下一个，谁先成功用谁。

        should_abort 返回 True 时在下一次"发请求"的边界放弃（弹窗已被关，没必要
        把整条链跑完再占着网络）。NO_HEDGE 里的端点（DeepL）只在没有请求在飞时
        才发——它限流凶，不能拿来对冲。
        """
        if not text.strip():
            return ""
        with self._state_lock:
            pending = self._order(time.time())
        running: Dict[Future, Tuple[str, float]] = {}
        errors: List[str] = []
        next_launch_at = 0.0

        def record_failure(name: str, t0: float, exc: BaseException) -> None:
            with self._state_lock:
                self._cooldown[name] = time.time() + COOLDOWN_S
            errors.append(f"{name}:{exc.__class__.__name__}")
            log.info("免费引擎 %s 失败（%.1fs，冷却 %ds）：%s",
                     name, time.monotonic() - t0, COOLDOWN_S, exc)

        while True:
            now = time.monotonic()
            can_launch = bool(pending) and (
                not running or (now >= next_launch_at and pending[0][0] not in NO_HEDGE))
            if can_launch:
                if should_abort is not None and should_abort():
                    raise LLMError("已取消")
                name, fn = pending.pop(0)
                fut = _hedge_pool.submit(fn, text, target_language)
                running[fut] = (name, now)
                next_launch_at = now + stagger_s
                if running and len(running) > 1:
                    log.info("免费引擎对冲：%s 未在 %.2fs 内返回，补发 %s",
                             "/".join(n for n, _ in running.values() if n != name),
                             stagger_s, name)
                continue
            if not running:
                break
            # 有请求在飞：等到它回来，或到了该补发下一个的时刻；期间每 0.25s 看一眼取消
            if pending and pending[0][0] not in NO_HEDGE:
                timeout = max(0.0, next_launch_at - now)
            else:
                timeout = 0.25
            done, _ = wait(list(running), timeout=min(timeout, 0.25) if timeout else 0,
                           return_when=FIRST_COMPLETED)
            if not done and should_abort is not None and should_abort():
                # 弹窗已关：在飞的请求跑完就丢，不再补发
                raise LLMError("已取消")
            for fut in done:
                name, t0 = running.pop(fut)
                try:
                    result = fut.result()
                    if not result.strip():
                        raise LLMError("空结果")
                except Exception as e:
                    record_failure(name, t0, e)
                    continue
                with self._state_lock:
                    self._preferred = name
                    self._cooldown.pop(name, None)
                log.info("免费引擎命中：%s（%.2fs）", name, time.monotonic() - t0)
                # 还在飞的对冲请求：成功就算了，失败照常记冷却，别让坏端点下次又当首发
                for other, (oname, ot0) in running.items():
                    other.add_done_callback(
                        lambda f, n=oname, t=ot0: (
                            record_failure(n, t, f.exception()) if f.exception() else None))
                return result
        raise LLMError("免费翻译暂不可用（" + " ".join(errors) + "），可在设置里配置大模型")

    def test_connection(self) -> str:
        r = self.translate("hello", "zh-CN")
        if not r.strip():
            raise LLMError("免费引擎返回空")
        return f"免费引擎可用（{self._preferred}）"


free_engine = FreeEngine()


def resolve_engine(cfg):
    """按配置解析翻译引擎。

    translate.engine: auto(默认) / free / llm
     - auto：配置了 API Key 用大模型，否则用免费引擎
     - free：始终免费引擎
     - llm ：始终大模型（未配置时报友好错误）
    """
    from .llm import client_from_config

    mode = cfg.get("translate.engine", "auto")
    if mode == "free":
        return free_engine
    if mode == "llm":
        return client_from_config(cfg)
    api_key = (cfg.get("provider.api_key") or "").strip()
    return client_from_config(cfg) if api_key else free_engine


def uses_free_engine(cfg) -> bool:
    """当前配置下这次翻译会不会走免费引擎（纯谓词，可单测）。

    界面上要按引擎给提示（免费引擎超过 MAX_CHUNK 会分段），但不能为了问一句
    就去调 resolve_engine：那会真的构造一个 LLM 客户端，llm 模式没配 Key 时还
    直接抛 LLMError。判定条件必须和上面 resolve_engine 保持一致，改一处要改两处。
    """
    mode = cfg.get("translate.engine", "auto")
    if mode == "free":
        return True
    if mode == "llm":
        return False
    return not (cfg.get("provider.api_key") or "").strip()
