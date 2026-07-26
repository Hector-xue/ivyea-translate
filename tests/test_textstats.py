"""字符统计口径：纯函数，不需要 Qt。

界面上那个数字是用户拿去填亚马逊标题/五点的，口径错了比不显示还糟，
所以每种文字都钉死一个用例。
"""
import pytest

from ivyea_translate import textstats as ts


@pytest.mark.parametrize("text, chars, no_space, words, lines", [
    ("翻译软件", 4, 4, 4, 1),                       # CJK 逐字计词
    ("hello world", 11, 10, 2, 1),                  # 拉丁按词
    ("翻译 hello world 软件", 17, 14, 6, 1),        # 混排各算各的
    ("こんにちは", 5, 5, 5, 1),                     # 假名
    ("안녕하세요", 5, 5, 5, 1),                     # 韩文
    ("Café naïve", 10, 9, 2, 1),                    # 带变音符仍是 2 个词
    ("你好\nworld", 8, 7, 3, 2),                    # 换行计行
    ("", 0, 0, 0, 0),                               # 空文本全 0
])
def test_count_covers_each_script(text, chars, no_space, words, lines):
    s = ts.count(text)
    assert (s.chars, s.chars_no_space, s.words, s.lines) == (chars, no_space, words, lines)


def test_hyphenated_and_apostrophe_words_count_as_one():
    """don't / state-of-the-art 是一个词，切碎了英文写作的词数就没意义了。"""
    assert ts.count("don't state-of-the-art").words == 2


def test_full_width_space_counts_as_whitespace():
    """中文输入法的全角空格也是空白，不能算进"不含空格"。"""
    s = ts.count("你好　世界")   # U+3000
    assert s.chars == 5 and s.chars_no_space == 4


def test_none_is_treated_as_empty():
    assert ts.count(None).chars == 0


def test_brief_hides_zero():
    """空框下写"0 字符"是纯噪音，必须是空串。"""
    assert ts.brief(ts.count("")) == ""
    assert ts.brief(ts.count("abc")) == "3 字符"


def test_detail_lists_all_four():
    d = ts.detail(ts.count("翻译 hello"))
    assert "含空格 8" in d and "不含空格 7" in d and "3 词" in d and "1 行" in d
    assert ts.detail(ts.count("")) == ""


@pytest.mark.parametrize("src, dst, compact, expect", [
    (128, 96, False, "128 → 96 字符"),
    (128, 96, True, "128→96"),          # 窄弹窗 / 原位工具条
    (128, 0, False, "128 字符"),         # 还没翻出来
    (128, 0, True, "128"),
    (0, 96, False, "96 字符"),           # 没原文时别写成 "0 → 96"
    (0, 96, True, "96"),
    (0, 0, False, ""),                  # 什么都没有就别占位置
])
def test_pair_brief(src, dst, compact, expect):
    assert ts.pair_brief(src, dst, compact) == expect


def test_pair_detail_labels_both_sides():
    d = ts.pair_detail("hello", "你好")
    assert d.startswith("原文：") and "译文：" in d
    assert ts.pair_detail("", "") == ""
