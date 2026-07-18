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
