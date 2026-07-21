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
