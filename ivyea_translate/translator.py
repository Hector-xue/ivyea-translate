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


def build_email_messages(text: str, target_language: str, tone: str) -> List[Dict[str, str]]:
    """邮件优化 prompt。纯函数。

    要求模型：按目标语言母语者的邮件惯例重写（称呼/正文/结尾），
    不直译不添油加醋，并总结一行主题；输出用固定标记便于解析。
    """
    lang_name = LANGUAGE_NAMES.get(target_language, target_language)
    tone_rule = EMAIL_TONES.get(tone, EMAIL_TONES["business"])[1]
    system = (
        "You are a professional bilingual email assistant. "
        f"Rewrite the user's draft as a polished, natural email in {lang_name}, "
        "following the email conventions native speakers actually use "
        "(appropriate greeting, well-organized body, proper closing). "
        f"{tone_rule} "
        "Preserve every fact in the draft; do not invent information. "
        "If the draft lacks names, use natural generic forms (e.g. 'Hi team,' / '尊敬的客户'). "
        "Also write ONE concise subject line in the same target language that summarizes the email. "
        "Output EXACTLY in this format, nothing else:\n"
        f"{SUBJECT_MARK}<subject line>\n"
        f"{BODY_MARK}\n"
        "<email body>"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": text},
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
