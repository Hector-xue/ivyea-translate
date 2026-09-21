"""Prompt 编译 + 流式翻译。

build_messages 是纯函数（可单测）：目标语言 + 风格规则 -> chat messages。
TranslateWorker 是 QThread 包装：流式增量经信号发给 UI 线程。
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, QThread, Signal

from .llm import LLMClient, LLMError

# 翻译结果缓存（内存 LRU）：同一段文字重复翻译秒出、少发一次请求
_CACHE: "OrderedDict[Tuple, str]" = OrderedDict()
_CACHE_MAX = 300


def cache_get(key: Tuple) -> Optional[str]:
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key]
    return None


def cache_put(key: Tuple, value: str) -> None:
    _CACHE[key] = value
    _CACHE.move_to_end(key)
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)

# prompt 内用英文语言名，模型遵循度最好
LANGUAGE_NAMES: Dict[str, str] = {
    "zh-CN": "Simplified Chinese",
    "zh-TW": "Traditional Chinese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "ru": "Russian",
    "pt": "Portuguese",
    "it": "Italian",
    "ar": "Arabic",
    "vi": "Vietnamese",
    "th": "Thai",
}

# 风格 -> 附加规则。american/british 仅对英语目标生效（其他目标语言忽略，见 build_messages）
STYLE_RULES: Dict[str, str] = {
    "general": "",
    "american": (
        "Use American English: American spelling (color, organize, center), "
        "American vocabulary and idioms (apartment, truck, vacation), and American punctuation conventions."
    ),
    "british": (
        "Use British English: British spelling (colour, organise, centre), "
        "British vocabulary and idioms (flat, lorry, holiday), and British punctuation conventions."
    ),
    "formal": "Use a formal, polished register suitable for business and official documents.",
    "casual": "Use a casual, conversational register with natural everyday expressions.",
    "academic": "Use precise academic register with rigorous terminology, suitable for papers and reports.",
    "concise": "Prefer the most concise natural phrasing; trim redundancy while preserving full meaning.",
}

ENGLISH_ONLY_STYLES = {"american", "british"}

# ---------- 邮件助手 ----------

# 邮件语气：code -> (中文名, prompt 规则)
EMAIL_TONES: Dict[str, tuple] = {
    "business": ("商务正式", "Use a polished, formal business tone."),
    "friendly": ("友好亲和", "Use a warm, friendly yet professional tone."),
    "concise": ("简洁高效", "Be brief and to the point; cut all pleasantries that carry no information."),
    "urgent": ("礼貌催促", "Convey polite but firm urgency appropriate for a follow-up or reminder."),
    "apologetic": ("致歉安抚", "Use a sincere, accountable, reassuring tone suitable for an apology."),
    "thankful": ("感谢致意", "Express genuine appreciation and goodwill."),
}

SUBJECT_MARK = "【主题】"
BODY_MARK = "【正文】"

# 写作场景：code -> (中文名, 英文描述给模型, 是否需要主题行)
COMPOSE_SCENARIOS: Dict[str, tuple] = {
    "email": ("邮件", "a polished, well-structured email with an appropriate greeting and closing", True),
    "message": ("消息/聊天", "a short, natural instant message (e.g. Slack, WeChat, Teams) — conversational, no greeting/signature", False),
    "comment": ("评论/PR", "a concise, professional comment for a code review, GitHub PR or issue — clear and to the point", False),
    "social": ("社媒/贴文", "an engaging social-media post, natural and idiomatic for the platform", False),
    "general": ("通用", "a polished, natural piece of writing", False),
}


def build_compose_messages(text: str, target_language: str, scenario: str, tone: str) -> List[Dict[str, str]]:
    """反向写作 prompt（纯函数）：把用户草稿改写成目标语言母语者写法。

    email 场景额外产出一行主题；其余场景只出正文。不直译、不添油加醋。
    """
    lang_name = LANGUAGE_NAMES.get(target_language, target_language)
    tone_rule = EMAIL_TONES.get(tone, EMAIL_TONES["business"])[1]
    _name, scen_desc, want_subject = COMPOSE_SCENARIOS.get(scenario, COMPOSE_SCENARIOS["general"])
    lines = [
        "You are a professional bilingual writing assistant.",
        f"Rewrite the user's draft as {scen_desc}, in {lang_name}, "
        "natural and idiomatic exactly as a native speaker would write it.",
        tone_rule,
        "Preserve every fact in the draft; do not invent information.",
        "If names are missing, use natural generic forms.",
    ]
    if want_subject:
        lines.append(
            "Also write ONE concise subject line in the target language. "
            "Output EXACTLY in this format, nothing else:\n"
            f"{SUBJECT_MARK}<subject line>\n{BODY_MARK}\n<body>"
        )
    else:
        lines.append("Output ONLY the rewritten text, nothing else (no preamble, no quotes).")
    return [
        {"role": "system", "content": " ".join(lines[:-1]) + "\n" + lines[-1]},
        {"role": "user", "content": text},
    ]


def parse_compose_output(text: str, scenario: str) -> tuple:
    """按场景解析输出。email -> (主题, 正文)；其余 -> ("", 正文)。"""
    if scenario == "email":
        return parse_email_output(text)
    return "", text.strip()


def build_email_messages(text: str, target_language: str, tone: str) -> List[Dict[str, str]]:
    """邮件优化 = 写作助手的 email 场景（保留旧接口）。"""
    return build_compose_messages(text, target_language, "email", tone)


# ---------- 详解模式（学习）----------

def build_explain_messages(focus_text: str, reference: str, explain_language: str) -> List[Dict[str, str]]:
    """给语言学习者的讲解 prompt（纯函数）。

    focus_text = 要讲解的外语文本；reference = 其译文（母语）作参考；
    讲解用 explain_language（母语）书写，简洁实用。
    """
    lang_name = LANGUAGE_NAMES.get(explain_language, explain_language)
    system = (
        "You are a concise, practical language tutor helping a learner. "
        f"Write the explanation in {lang_name}. "
        "You are given a FOREIGN text and its TRANSLATION. Explain the FOREIGN text so the learner "
        "understands it and could use it. Cover, briefly and only when relevant:\n"
        "1) key words / phrases with pronunciation (pinyin for Chinese, kana for Japanese, IPA otherwise) and meaning;\n"
        "2) one or two grammar / sentence-structure points;\n"
        "3) tone/register and 1–2 more natural alternative phrasings.\n"
        "Use short bullet lines. Be concise. Do NOT restate the whole translation."
    )
    user = f"FOREIGN:\n{focus_text}\n\nTRANSLATION:\n{reference}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def parse_email_output(text: str) -> tuple:
    """把模型输出拆成 (主题, 正文)。标记缺失时优雅降级。"""
    subject = ""
    body = text.strip()
    if SUBJECT_MARK in text:
        after = text.split(SUBJECT_MARK, 1)[1]
        if BODY_MARK in after:
            subject_part, body = after.split(BODY_MARK, 1)
        else:
            lines = after.split("\n", 1)
            subject_part, body = lines[0], (lines[1] if len(lines) > 1 else "")
        subject = subject_part.strip()
        body = body.strip()
    elif body.lower().startswith("subject:"):
        lines = body.split("\n", 1)
        subject = lines[0][len("subject:"):].strip()
        body = (lines[1] if len(lines) > 1 else "").strip()
    return subject, body


def build_messages(text: str, target_language: str, style: str) -> List[Dict[str, str]]:
    """编译翻译请求的 messages。纯函数。

    - 非英语目标 + 美式/英式风格：风格自动降级为 general（规则不注入）。
    - system 强约束只输出译文；原文放 user，避免 prompt 注入原文指令。
    """
    lang_name = LANGUAGE_NAMES.get(target_language, target_language)
    rules = [
        "You are a professional translation engine.",
        f"Translate the user's text into {lang_name}.",
        "Output ONLY the translation. No explanations, no notes, no quotes around it.",
        "Preserve the original paragraph breaks and inline formatting (numbers, URLs, code, placeholders).",
        "If the text is already entirely in the target language, polish it minimally and output it.",
    ]
    style_rule = STYLE_RULES.get(style, "")
    if style in ENGLISH_ONLY_STYLES and target_language != "en":
        style_rule = ""
    if style_rule:
        rules.append(style_rule)
    return [
        {"role": "system", "content": " ".join(rules)},
        {"role": "user", "content": text},
    ]


def cache_key_for(client, target_language: str, style: str, text: str) -> Tuple:
    """翻译缓存键：引擎身份 + 目标语言 + 风格 + 原文（弹窗/原位/主窗共用）。"""
    eng = "free" if getattr(client, "is_free", False) else \
        f"{getattr(client, 'base_url', '')}|{getattr(client, 'model', '')}"
    return (eng, target_language, style, text)


class ParagraphTranslator(QObject):
    """段落级并发翻译：OCR 每交出一段就 submit 一段，翻完一段发一段。

    截图翻译以前是"整图识完 → 整段一次请求 → 回来才显示"；现在 OCR 流水线逐段
    交付，这里逐段翻译（并发 3），首段译文在首段识别完后一个往返就能出现，
    与 OCR 剩余段落的识别重叠。大模型引擎逐段流式（chunk 信号带段号），免费引擎
    一段一次返回。

    "哪段译文属于哪段原文"由段号保证，不依赖模型守规矩。取消后在飞的请求结果
    一律丢弃（信号不再发）。
    """

    chunk = Signal(int, str)     # 段号, 增量（仅大模型流式）
    done = Signal(int, str)      # 段号, 译文
    failed = Signal(int, str)    # 段号, 错误

    MAX_WORKERS = 3

    def __init__(self, client: LLMClient, target_language: str, style: str, parent=None):
        super().__init__(parent)
        self._client = client
        self._target = target_language
        self._style = style
        self._cancelled = False
        self._pool = None

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def submit(self, idx: int, text: str) -> None:
        """提交一段（可从任意线程调用）。空段直接回 done("")。"""
        if self._cancelled:
            return
        if not text.strip():
            self.done.emit(idx, "")
            return
        key = cache_key_for(self._client, self._target, self._style, text)
        cached = cache_get(key)
        if cached is not None:
            self.done.emit(idx, cached)
            return
        if self._pool is None:
            from concurrent.futures import ThreadPoolExecutor

            self._pool = ThreadPoolExecutor(
                max_workers=self.MAX_WORKERS, thread_name_prefix="para-translate")
        self._pool.submit(self._run_one, idx, text, key)

    def cancel(self) -> None:
        self._cancelled = True
        pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=False)

    def _run_one(self, idx: int, text: str, key: Tuple) -> None:
        if self._cancelled:
            return
        try:
            if getattr(self._client, "is_free", False):
                result = self._client.translate(
                    text, self._target, should_abort=lambda: self._cancelled)
            else:
                parts: List[str] = []
                for piece in self._client.stream_chat(
                        messages_for(self._client, text, self._target, self._style)):
                    if self._cancelled:
                        return
                    parts.append(piece)
                    self.chunk.emit(idx, piece)
                result = "".join(parts)
        except LLMError as e:
            if not self._cancelled:
                self.failed.emit(idx, str(e))
            return
        except Exception as e:
            if not self._cancelled:
                self.failed.emit(idx, f"{e.__class__.__name__}: {e}")
            return
        if self._cancelled:
            return
        result = result.strip()
        if result:
            cache_put(key, result)
        self.done.emit(idx, result)


def build_local_messages(text: str, target_language: str, style: str) -> List[Dict[str, str]]:
    """本地 Hy-MT 模型的翻译 prompt（纯函数），照官方 README 模板写：

    - 模型没有默认 system prompt，指令全在 user 消息里；
    - 默认模板："Translate the following text into X. Note that you should only
      output the translated result without any additional explanation:\n\n{text}"
    - 带风格时用官方 Style 模板（"translation style must strictly conform to [...]"）。
    小模型对模板敏感，别拿云端那套长 system 规则去喂它。
    """
    lang_name = LANGUAGE_NAMES.get(target_language, target_language)
    style_rule = STYLE_RULES.get(style, "")
    if style in ENGLISH_ONLY_STYLES and target_language != "en":
        style_rule = ""
    if style_rule:
        prompt = (
            f"Please translate the following text into {lang_name}. "
            f"Note that the translation style must strictly conform to [{style_rule}]. "
            "Only output the translated result without any additional explanation:\n\n"
            f"{text}"
        )
    else:
        prompt = (
            f"Translate the following text into {lang_name}. "
            "Note that you should only output the translated result without any additional explanation:\n\n"
            f"{text}"
        )
    return [{"role": "user", "content": prompt}]


def messages_for(client, text: str, target_language: str, style: str) -> List[Dict[str, str]]:
    """按客户端类型选 prompt：本地小模型用官方模板，云端大模型用完整规则。"""
    if getattr(client, "is_local", False):
        return build_local_messages(text, target_language, style)
    return build_messages(text, target_language, style)


class BlockTranslateWorker(QThread):
    """原位翻译专用：逐块翻译，每翻完一块就发一次。

    早期版本把所有块拼成一次请求、再按空行切回，只要模型少给一个空行，整屏
    译文就会集体错位贴到别人的段落上。改成一块一次请求后，"哪段译文属于哪个框"
    由结构保证，不再依赖模型守规矩。

    并发 2：多段截图近乎倍速。保守取 2——免费引擎不至于触发限流（真撞上也有
    回退链兜底），自定义地址指向本地单实例模型（ollama 等）最多排队、不劣化。
    """

    block_done = Signal(int, str)   # 块序号, 译文
    block_failed = Signal(int, str)
    finished_all = Signal()

    def __init__(self, client: LLMClient, texts: List[str], target_language: str,
                 style: str, parent=None):
        super().__init__(parent)
        self._client = client
        self._texts = list(texts)
        self._target_language = target_language
        self._style = style
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _translate_one(self, idx: int, text: str) -> None:
        if self._cancelled:
            return
        try:
            if getattr(self._client, "is_free", False):
                result = self._client.translate(
                    text, self._target_language,
                    should_abort=lambda: self._cancelled)
            else:
                messages = messages_for(self._client, text, self._target_language, self._style)
                result = "".join(self._client.stream_chat(messages))
        except LLMError as e:
            if not self._cancelled:
                self.block_failed.emit(idx, str(e))
            return
        except Exception as e:
            if not self._cancelled:
                self.block_failed.emit(idx, f"{e.__class__.__name__}: {e}")
            return
        if not self._cancelled:
            self.block_done.emit(idx, result.strip())

    def run(self) -> None:
        todo = [(i, t) for i, t in enumerate(self._texts) if t.strip()]
        if len(todo) <= 1:
            for idx, text in todo:
                self._translate_one(idx, text)
        else:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=2) as ex:
                for fut in [ex.submit(self._translate_one, i, t) for i, t in todo]:
                    fut.result()
        if not self._cancelled:
            self.finished_all.emit()


class TranslateWorker(QThread):
    """后台流式翻译线程。chunk 信号发增量文本，finished_ok 发完整结果，failed 发错误信息。"""

    chunk = Signal(str)
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, client: LLMClient, text: str, target_language: str, style: str,
                 parent=None, messages: List[Dict[str, str]] = None):
        super().__init__(parent)
        self._client = client
        self._text = text
        self._target_language = target_language
        self._style = style
        self._messages = messages  # 传入则直接用（邮件助手等定制 prompt）
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        parts: List[str] = []
        try:
            # 仅缓存普通翻译（定制 prompt 如邮件/详解一次性，不缓存）
            key = None
            if self._messages is None:
                key = cache_key_for(self._client, self._target_language, self._style, self._text)
                cached = cache_get(key)
                if cached is not None:
                    if self._cancelled:
                        return
                    self.chunk.emit(cached)
                    self.finished_ok.emit(cached)
                    return
            if self._messages is None and getattr(self._client, "is_free", False):
                # 免费引擎：非流式，一次返回整段；弹窗被关就别把回退链跑完
                result = self._client.translate(
                    self._text, self._target_language,
                    should_abort=lambda: self._cancelled)
                if self._cancelled:
                    return
                self.chunk.emit(result)
                self.finished_ok.emit(result)
                if key:
                    cache_put(key, result)
                return
            messages = self._messages or messages_for(
                self._client, self._text, self._target_language, self._style)
            for piece in self._client.stream_chat(messages):
                if self._cancelled:
                    return
                parts.append(piece)
                self.chunk.emit(piece)
            full = "".join(parts)
            self.finished_ok.emit(full)
            if key and full.strip():
                cache_put(key, full)
        except LLMError as e:
            if not self._cancelled:
                self.failed.emit(str(e))
        except Exception as e:  # 不让线程静默崩掉
            if not self._cancelled:
                self.failed.emit(f"翻译失败：{e.__class__.__name__}: {e}")
