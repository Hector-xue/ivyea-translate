"""弹窗外观件：品牌小标、图钉图标、抓边带判定。"""
from PySide6.QtCore import QPoint


def _popup(qapp, **kw):
    from ivyea_translate.ui.popup import TranslationPopup

    return TranslationPopup(**kw)


def test_popup_shows_brand(qapp):
    from ivyea_translate.ui.popup import BRAND_NAME

    p = _popup(qapp)
    labels = [w.text() for w in p.findChildren(type(p.status_label))]
    assert BRAND_NAME in labels
    p.deleteLater()


def test_popup_brand_name_hidden_when_narrow(qapp):
    from ivyea_translate.ui.popup import BRAND_NAME

    p = _popup(qapp, width=380)
    brand = [w for w in p.findChildren(type(p.status_label)) if w.text() == BRAND_NAME][0]
    assert not brand.isVisibleTo(p)  # 窄弹窗只留 logo，别把状态文字挤没
    p.deleteLater()


def test_pin_uses_drawn_icon_not_emoji(qapp):
    """emoji 在 Win10 上会被 26x26 的按钮裁掉一角，必须是自绘图标。"""
    p = _popup(qapp)
    assert p.pin_btn.text() == ""
    assert not p.pin_btn.icon().isNull()
    p.deleteLater()


def test_pin_icon_fits_button(qapp):
    from ivyea_translate.ui.widgets import pin_icon

    icon = pin_icon("#6BA53F", 15)
    pm = icon.pixmap(15, 15)
    assert not pm.isNull()
    img = pm.toImage()
    # 图标画满可用高度但不越界：首末行透明，中间有像素
    rows = [
        any(img.pixelColor(x, y).alpha() > 0 for x in range(img.width()))
        for y in range(img.height())
    ]
    assert any(rows)
    assert rows[0] is False and rows[-1] is False


def test_pin_toggle_keeps_brand_color(qapp):
    from ivyea_translate.ui import theme

    p = _popup(qapp)
    assert not p._pinned
    p._toggle_pin()
    assert p._pinned
    assert theme.ACCENT_SOFT in p.pin_btn.styleSheet()  # 钉住态有品牌绿底衬
    assert not p.pin_btn.icon().isNull()
    p.deleteLater()


def test_pin_toggle_emits_signal_and_property(qapp):
    """app 靠 pin_toggled/is_pinned 决定全局"点外即关"监听的起停。"""
    p = _popup(qapp)
    seen = []
    p.pin_toggled.connect(seen.append)
    assert p.is_pinned is False
    p._toggle_pin()
    assert p.is_pinned is True
    p._toggle_pin()
    assert seen == [True, False]
    p.deleteLater()


def test_card_border_is_visible(qapp):
    """bug：旧边框是 rgba(255,255,255,0.9)，浅色背景上等于没有边框。"""
    from ivyea_translate.ui import theme

    p = _popup(qapp)
    qss = p._card.styleSheet()
    assert theme.CARD_BORDER in qss
    assert "rgba(255, 255, 255, 0.9)" not in qss
    p.deleteLater()


def test_copy_menu_offers_both_texts(qapp):
    """复制按钮弹两项菜单；原文为空（OCR 未回填）时"复制原文"置灰。"""
    p = _popup(qapp, original="", show_original=True)
    p.set_done("译文")
    menu = p._build_copy_menu()
    labels = [a.text() for a in menu.actions()]
    assert labels == ["复制译文", "复制原文"]
    assert not menu.actions()[1].isEnabled()

    p.set_original("source text")
    menu2 = p._build_copy_menu()
    assert menu2.actions()[1].isEnabled()
    p.deleteLater()


def test_copy_menu_actions_write_clipboard(qapp):
    from PySide6.QtGui import QGuiApplication

    p = _popup(qapp, original="src", show_original=True)
    p.set_done("res")
    menu = p._build_copy_menu()
    menu.actions()[0].trigger()
    assert QGuiApplication.clipboard().text() == "res"
    menu.actions()[1].trigger()
    assert QGuiApplication.clipboard().text() == "src"
    assert p.copy_btn.text() == "已复制"   # 按钮给出反馈，1.2s 后自动复原
    p.deleteLater()


def test_titlebar_row_is_drag_not_resize(qapp):
    """标题行是拖动把手：光标必须是箭头，不能是上下缩放箭头。"""
    from PySide6.QtCore import Qt

    p = _popup(qapp)
    p.resize(520, 360)
    card = p._card_rect()
    x = card.center().x()
    # 卡片内往下 8px 起（标题行文字/按钮所在）不再算上边缘
    assert p._edge_at(QPoint(x, card.top() + 8)) == ""
    assert p._CURSORS.get(p._edge_at(QPoint(x, card.top() + 8)), Qt.ArrowCursor) == Qt.ArrowCursor
    # 卡片外那圈留白仍然可以抓上边缩放
    assert p._edge_at(QPoint(x, card.top() - 6)) == "top"
    assert p._edge_at(QPoint(card.left() - 4, card.center().y())) == "left"
    assert p._edge_at(QPoint(card.right(), card.bottom())) == "bottomright"
    assert p._edge_at(card.center()) == ""
    p.deleteLater()


def test_manual_resize_gives_extra_height_to_result(qapp):
    """拉大弹窗时富余高度只给译文区；原文区仍按内容高度，分界线贴着原文底。"""
    p = _popup(qapp, original="", show_original=True)
    p.show()
    qapp.processEvents()
    p.set_original("One short line of source text.")
    p.set_done("一行很短的译文。")
    qapp.processEvents()
    p.enter_manual_size()
    p.resize(600, 700)
    qapp.processEvents()
    qapp.processEvents()

    # 原文区高度 = max(内容高度, 文本框设计下限 60) + 少量内边距，与窗口高度无关
    content_h = max(p._orig_view.document().size().height(), 60)
    assert p._orig_view.height() <= content_h + 24   # 没吃掉富余空间
    assert p.result_view.height() > p._orig_view.height() * 3
    p.deleteLater()


def test_status_flips_to_done_together_with_text(qapp):
    """流式片段是合并刷新的，set_done 必须当场落定文本与状态。"""
    p = _popup(qapp)
    p.show()
    qapp.processEvents()
    for piece in ["你好", "，", "世界"]:
        p.append_chunk(piece)
    p.set_done("你好，世界")
    assert p.status_label.text() == "已翻译"
    assert p.result_view.toPlainText() == "你好，世界"
    assert not p._flush_timer.isActive()
    p.deleteLater()


def test_streaming_flush_appends_without_losing_text(qapp):
    """合并刷新是追加而不是整篇重设：刷完内容要和收到的片段一致。"""
    p = _popup(qapp)
    p.show()
    qapp.processEvents()
    for piece in ["abc", "def", "ghi"]:
        p.append_chunk(piece)
    p._on_flush_timeout()          # 直接触发一次合并刷新
    assert p.result_view.toPlainText() == "abcdefghi"
    p.append_chunk("jkl")
    p._on_flush_timeout()
    assert p.result_view.toPlainText() == "abcdefghijkl"
    p.deleteLater()


def test_cursor_returns_to_arrow_inside_card(qapp):
    """贴过边之后把鼠标移回卡片内部，光标必须变回普通箭头（v0.24.0 会卡住）。"""
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    p = _popup(qapp)
    p.resize(520, 360)
    card = p._card_rect()
    p._sync_hover_cursor(QPoint(card.center().x(), card.top() - 6))
    assert p.cursor().shape() == Qt.SizeVerCursor

    # 通过真实的事件过滤器路径复位：鼠标移到标题行上的子控件
    child = p.status_label
    local = QPointF(child.rect().center())
    ev = QMouseEvent(QEvent.MouseMove, local, child.mapToGlobal(child.rect().center()),
                     Qt.NoButton, Qt.NoButton, Qt.NoModifier)
    p.eventFilter(child, ev)
    assert p.cursor().shape() == Qt.ArrowCursor
    p.deleteLater()
