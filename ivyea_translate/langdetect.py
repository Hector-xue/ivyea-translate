"""轻量语言方向判定：智能"翻成另一种语言"。

choose_target 是纯函数（可单测）：若文本看起来就是主语言，则翻成次语言，
否则翻成主语言。默认 主=中文、次=英文——中文选中→英文，其余→中文。
按字符脚本占比判定，覆盖常见语言，判不准时安全落回主语言（不误翻）。
"""
from __future__ import annotations

from typing import Dict, Tuple


def _script_counts(text: str) -> Tuple[Dict[str, int], int]:
    han = kana = hangul = latin = cyrillic = arabic = thai = 0
    for ch in text:
        o = ord(ch)
        if 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF:
            han += 1
        elif 0x3040 <= o <= 0x30FF:
            kana += 1
        elif 0xAC00 <= o <= 0xD7AF or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F:
            hangul += 1
        elif (0x41 <= o <= 0x5A or 0x61 <= o <= 0x7A
              or 0x00C0 <= o <= 0x024F or 0x1E00 <= o <= 0x1EFF):
            latin += 1
        elif 0x0400 <= o <= 0x04FF:
            cyrillic += 1
        elif 0x0600 <= o <= 0x06FF:
            arabic += 1
        elif 0x0E00 <= o <= 0x0E7F:
            thai += 1
    counts = {"han": han, "kana": kana, "hangul": hangul, "latin": latin,
              "cyrillic": cyrillic, "arabic": arabic, "thai": thai}
    total = han + kana + hangul + latin + cyrillic + arabic + thai
    return counts, total


_LATIN_LANGS = {"en", "fr", "de", "es", "it", "pt", "vi"}


def is_language(text: str, lang: str) -> bool:
    """文本的主要文字脚本是否匹配 lang。判不准返回 False。"""
    c, total = _script_counts(text)
    if total == 0:
        return False
    base = lang.split("-")[0]
    if base == "zh":
        return c["han"] / total > 0.5 and c["kana"] == 0 and c["hangul"] == 0
    if base == "ja":
        return c["kana"] > 0
    if base == "ko":
        return c["hangul"] / total > 0.3
    if base == "ru":
        return c["cyrillic"] / total > 0.5
    if base == "ar":
        return c["arabic"] / total > 0.5
    if base == "th":
        return c["thai"] / total > 0.5
    if base in _LATIN_LANGS:
        return (c["latin"] / total > 0.6 and c["han"] == 0
                and c["hangul"] == 0 and c["cyrillic"] == 0)
    return False


def choose_target(text: str, primary: str, secondary: str) -> str:
    """自动方向：文本是主语言 -> 翻成次语言；否则 -> 翻成主语言。"""
    if not primary:
        return secondary or "en"
    if primary == secondary:
        return primary
    if is_language(text, primary):
        return secondary
    return primary
