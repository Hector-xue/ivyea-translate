"""截图弹窗两段式：先'识别中'（原文区隐藏），set_original 后回填显示。"""


def test_popup_original_filled_later(qapp):
    from ivyea_translate.ui.popup import TranslationPopup

    p = TranslationPopup(original="", show_original=True)
    # OCR 还没回来：原文区隐藏
    assert p._orig_view is not None
    assert not p._orig_view.isVisibleTo(p)

    p.set_status("正在识别文字…")
    assert p.status_label.text() == "正在识别文字…"

    p.set_original("Hello OCR")
    assert p._orig_view.isVisibleTo(p)
    assert p._orig_view.toPlainText() == "Hello OCR"
    assert p.original_text == "Hello OCR"
    p.deleteLater()


def test_popup_with_original_upfront_still_works(qapp):
    from ivyea_translate.ui.popup import TranslationPopup

    p = TranslationPopup(original="预置原文", show_original=True)
    assert p._orig_view.isVisibleTo(p)
    assert p._orig_view.toPlainText() == "预置原文"
    p.deleteLater()


# ---------- 字符统计（并进状态行，不另起控件） ----------

def test_status_counts_original_then_pair(qapp):
    """OCR 回来先报原文字数，翻完换成"原文 → 译文"。"""
    from ivyea_translate.ui.popup import TranslationPopup

    p = TranslationPopup(original="", show_original=True)
    p.set_status("正在识别文字…")
    assert p.status_label.text() == "正在识别文字…"   # 什么都还没有，不占位

    p.set_original("Hello OCR")                       # 9 字符
    assert "9 字符" in p.status_label.text()

    p.set_status("翻译中…")
    assert p.status_label.text() == "翻译中… · 9 字符"

    p.set_done("你好")                                 # 2 字符
    assert p.status_label.text() == "已翻译 · 9 → 2 字符"
    p.deleteLater()


def test_selection_popup_counts_source_at_birth(qapp):
    """划词弹窗建的时候原文就有了，字符数当场可见，不用等翻译。"""
    from ivyea_translate.ui.popup import TranslationPopup

    p = TranslationPopup(original="hello world", show_original=False)
    assert "11 字符" in p.status_label.text()
    p.deleteLater()


def test_narrow_popup_uses_compact_counts(qapp):
    """窄弹窗横向空间就那么点，"128 → 96 字符"会把按钮挤出去。"""
    from ivyea_translate.ui.popup import TranslationPopup

    p = TranslationPopup(original="hello world", show_original=False, width=380)
    p.set_done("你好")
    assert p.status_label.text() == "已翻译 · 11→2"
    p.deleteLater()


def test_failure_status_drops_counts(qapp):
    """失败时状态行只留错误文案，再报数是添乱。"""
    from ivyea_translate.ui.popup import TranslationPopup

    p = TranslationPopup(original="hello world", show_original=False)
    p.set_failed("网络不通")
    assert p.status_label.text() == "失败"
    p.deleteLater()


def test_status_tooltip_has_both_sides(qapp):
    from ivyea_translate.ui.popup import TranslationPopup

    p = TranslationPopup(original="hello world", show_original=False)
    p.set_done("你好")
    tip = p.status_label.toolTip()
    assert "原文：" in tip and "译文：" in tip
    p.deleteLater()


def test_streaming_flush_updates_counts(qapp):
    """流式期间数字跟着 60ms 合并刷新走，不连 textChanged（那是性能老坑）。"""
    from ivyea_translate.ui.popup import TranslationPopup

    p = TranslationPopup(original="hello world", show_original=False)
    p.set_status("翻译中…")
    p.append_chunk("你好")
    p._on_flush_timeout()          # 直接催一次，不等定时器
    assert p.status_label.text() == "翻译中… · 11 → 2 字符"
    p.deleteLater()
