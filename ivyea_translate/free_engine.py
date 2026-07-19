"""内置免费翻译引擎：无需任何 Key，开箱即用。

内置两个公开端点，按顺序自动回退（不同网络环境各有可用的）：
  1. Google gtx（translate.googleapis.com）——无 token，纯 GET，最简单
  2. 必应网页接口（bing.com）——国内可直连

FreeEngine 记住上次成功的端点优先用；都失败才报错并建议配置大模型。
免费引擎不支持"风格"（美式/正式等），风格仅大模型引擎生效。
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Dict, List, Optional

import httpx

from .llm import LLMError

log = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

MAX_CHUNK = 900  # 单次请求字符上限

# 应用语言码 -> 各服务语言码
GOOGLE_LANG: Dict[str, str] = {
    "zh-CN": "zh-CN", "zh-TW": "zh-TW", "en": "en", "ja": "ja", "ko": "ko",
    "fr": "fr", "de": "de", "es": "es", "ru": "ru", "pt": "pt", "it": "it",
    "ar": "ar", "vi": "vi", "th": "th",
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
    return httpx.Client(timeout=15.0, follow_redirects=True, headers={"User-Agent": _UA})


# ---------- Google gtx ----------

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


def _google_translate(text: str, target_language: str) -> str:
    to_lang = GOOGLE_LANG.get(target_language, target_language)
    with _client() as c:
        out = [_google_chunk(c, chunk, to_lang) for chunk in split_for_translate(text)]
    return "\n".join(out) if len(out) > 1 else out[0]


# ---------- 必应 ----------

class _BingSession:
    def __init__(self):
        self.client: Optional[httpx.Client] = None
        self.ig = self.iid = self.key = self.token = None
        self.expire_at = 0.0
        self.iid_seq = 0

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
            self.token = None  # 票据失效
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


# ---------- 组合引擎 ----------

_ENGINES = [("google", _google_translate), ("bing", _bing_translate)]


class FreeEngine:
    """双端点自动回退。记住上次成功端点优先用。"""

    is_free = True

    def __init__(self):
        self._lock = threading.Lock()
        self._preferred: Optional[str] = None

    def translate(self, text: str, target_language: str) -> str:
        if not text.strip():
            return ""
        with self._lock:
            order = sorted(_ENGINES, key=lambda e: e[0] != self._preferred)
            errors = []
            for name, fn in order:
                try:
                    result = fn(text, target_language)
                    if result.strip():
                        self._preferred = name
                        log.info("免费引擎命中：%s", name)
                        return result
                except Exception as e:
                    errors.append(f"{name}:{e.__class__.__name__}")
                    log.info("免费引擎 %s 失败：%s", name, e)
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
