"""字符统计：翻译页、写作页、弹窗、原位覆盖层四处共用的纯函数。

口径按"用户拿去填表单"来定，不是按程序员的直觉：

chars（含空格）是主口径——亚马逊标题 200、五点 500、DeepL 计费，数的都是含空格
的字符数，界面上常显的就是它。chars_no_space / words / lines 只进 tooltip。

words 走 Word 的中文字数口径：CJK 逐字计（中文"翻译软件"是 4 个字，不是 1 个词），
拉丁按空白/标点切词。两种混排时各算各的再相加——这是唯一对中英混排都不离谱的算法。

零 Qt 依赖，全部可单测。
"""
from __future__ import annotations

import re
from typing import NamedTuple

# CJK：统一表意文字（含扩展 A、兼容区）+ 日文假名 + 韩文音节。
# 一律写 \u 转义而不是字面汉字：字面区间在源码被转码/规范化时会悄悄失效，
# 而这里出错的表现只是"词数偏小"，不报错、没人发现
_CJK_RE = re.compile(
    "[㐀-䶿一-鿿豈-﫿぀-ヿ가-힯]"
)
# 拉丁词：字母数字为主，允许词内撇号和连字符（don't、state-of-the-art 算一个词）
_LATIN_WORD_RE = re.compile(
    "[0-9A-Za-zÀ-ɏ]+(?:['’-][0-9A-Za-zÀ-ɏ]+)*"
)
_WS_RE = re.compile(r"\s")


class TextStats(NamedTuple):
    chars: int            # 含空格（主口径）
    chars_no_space: int   # 剔除所有空白字符
    words: int            # CJK 逐字 + 拉丁按词
    lines: int            # 空文本为 0


def count(text: str) -> TextStats:
    """统计一段文本。text 为 None/空串时全为 0。"""
    text = text or ""
    if not text:
        return TextStats(0, 0, 0, 0)
    # 两个正则的字符集不相交，中英混排不会重复计数
    cjk = len(_CJK_RE.findall(text))
    latin = len(_LATIN_WORD_RE.findall(text))
    return TextStats(
        chars=len(text),
        chars_no_space=len(_WS_RE.sub("", text)),
        words=cjk + latin,
        lines=len(text.splitlines()),
    )


def brief(stats: TextStats) -> str:
    """界面常显文案。空文本返回空串——占位状态下写"0 字符"是纯噪音。"""
    if not stats.chars:
        return ""
    return f"{stats.chars} 字符"


def detail(stats: TextStats) -> str:
    """悬停 tooltip 的完整口径。空文本返回空串。"""
    if not stats.chars:
        return ""
    return (f"含空格 {stats.chars} · 不含空格 {stats.chars_no_space} · "
            f"{stats.words} 词 · {stats.lines} 行")


def pair_brief(src_chars: int, dst_chars: int, compact: bool = False) -> str:
    """原文→译文的对照计数（弹窗状态行、原位工具条用）。

    compact 给窄弹窗和原位工具条：那两处横向空间是按像素抠出来的，
    "128 → 96 字符"会把按钮挤出去，收成"128→96"正好。
    """
    if not src_chars and not dst_chars:
        return ""
    # 一侧为空就只报另一侧："0 → 5 字符"没有信息量，只有噪音
    if not dst_chars:
        return f"{src_chars}" if compact else f"{src_chars} 字符"
    if not src_chars:
        return f"{dst_chars}" if compact else f"{dst_chars} 字符"
    if compact:
        return f"{src_chars}→{dst_chars}"
    return f"{src_chars} → {dst_chars} 字符"


def pair_detail(source: str, result: str) -> str:
    """对照 tooltip：原文与译文各自一行。"""
    parts = []
    s = count(source)
    if s.chars:
        parts.append(f"原文：{detail(s)}")
    r = count(result)
    if r.chars:
        parts.append(f"译文：{detail(r)}")
    return "\n".join(parts)
