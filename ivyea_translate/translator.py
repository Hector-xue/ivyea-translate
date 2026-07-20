"""Prompt 编译 + 流式翻译。

build_messages 是纯函数（可单测）：目标语言 + 风格规则 -> chat messages。
TranslateWorker 是 QThread 包装：流式增量经信号发给 UI 线程。
"""
from __future__ import annotations

from typing import Dict, List

from PySide6.QtCore import QThread, Signal

from .llm import LLMClient, LLMError

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
            if self._messages is None and getattr(self._client, "is_free", False):
                # 免费引擎：非流式，一次返回整段
                result = self._client.translate(self._text, self._target_language)
                if self._cancelled:
                    return
                self.chunk.emit(result)
                self.finished_ok.emit(result)
                return
            messages = self._messages or build_messages(self._text, self._target_language, self._style)
            for piece in self._client.stream_chat(messages):
                if self._cancelled:
                    return
                parts.append(piece)
                self.chunk.emit(piece)
            self.finished_ok.emit("".join(parts))
        except LLMError as e:
            if not self._cancelled:
                self.failed.emit(str(e))
        except Exception as e:  # 不让线程静默崩掉
            if not self._cancelled:
                self.failed.emit(f"翻译失败：{e.__class__.__name__}: {e}")
