"""原位翻译：坐标换算、段落合并、卡片排版、覆盖层交互。"""
import pytest

from ivyea_translate.ocr import OcrBlock, bounding_block


# ---------- 坐标换算（纯函数） ----------

@pytest.mark.parametrize("dpr, expect_xy, expect_wh", [
    (1.0, (97, 47), (206, 46)),
    (1.5, (64, 30), (139, 33)),   # 100/1.5=66.7→67，减 3px 内边距
    (2.0, (47, 22), (106, 26)),
])
def test_block_rect_maps_physical_to_logical(dpr, expect_xy, expect_wh):
    """物理像素框按屏幕缩放折回逻辑坐标；带 3px 内边距。"""
    from ivyea_translate.ui.inplace_overlay import block_rect

    r = block_rect(100, 50, 200, 40, dpr)
    assert (r.x(), r.y()) == expect_xy
    assert (r.width(), r.height()) == expect_wh


def test_block_rect_never_goes_negative():
    """贴着选区左上角的文字，加内边距后不能跑到窗口外。"""
    from ivyea_translate.ui.inplace_overlay import block_rect

    r = block_rect(0, 0, 10, 10, 1.0)
    assert r.x() == 0 and r.y() == 0


# ---------- 段落合并（原位专用） ----------

def test_merge_near_blocks_joins_close_paragraphs():
    """OCR 的分段偏碎，贴回屏幕会是一堆小卡片；靠得近的要并成一块。"""
    from ivyea_translate.ocr import merge_near_blocks

    blocks = [OcrBlock("first", 20, 20, 300, 30, line_h=15, lines=2),
              OcrBlock("second", 20, 62, 300, 30, line_h=15, lines=2)]  # 间距 12 < 1.8×15
    merged = merge_near_blocks(blocks, gap_factor=1.8)
    assert len(merged) == 1
    assert merged[0].text == "first\n\nsecond"
    assert merged[0].y == 20 and merged[0].y + merged[0].h == 92


def test_merge_near_blocks_keeps_far_paragraphs_apart():
    from ivyea_translate.ocr import merge_near_blocks

    blocks = [OcrBlock("first", 20, 20, 300, 30, line_h=15),
              OcrBlock("second", 20, 200, 300, 30, line_h=15)]
    assert len(merge_near_blocks(blocks, gap_factor=1.8)) == 2


def test_merge_near_blocks_keeps_side_by_side_columns():
    """左右分栏不能并：横向几乎不重叠说明是两栏，不是上下文。"""
    from ivyea_translate.ocr import merge_near_blocks

    blocks = [OcrBlock("left", 0, 20, 200, 30, line_h=15),
              OcrBlock("right", 400, 30, 200, 30, line_h=15)]
    assert len(merge_near_blocks(blocks, gap_factor=1.8)) == 2


# ---------- 降级用的大框 ----------

def test_bounding_block_covers_all():
    blocks = [OcrBlock("a", 10, 10, 100, 20, line_h=20),
              OcrBlock("b", 30, 50, 200, 40, line_h=20)]
    whole = bounding_block(blocks)
    assert (whole.x, whole.y) == (10, 10)
    assert whole.x + whole.w == 230
    assert whole.y + whole.h == 90
    assert whole.text == "a\n\nb"


# ---------- 覆盖层 ----------

def _shot(qapp, w=400, h=200):
    from PySide6.QtGui import QColor, QPixmap

    pm = QPixmap(w, h)
    pm.fill(QColor("#EEEEEE"))
    return pm


def test_overlay_cards_land_on_block_positions(qapp):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QColor

    from ivyea_translate.ui.inplace_overlay import InPlaceOverlay

    ov = InPlaceOverlay(QRect(100, 80, 400, 200), _shot(qapp), 1.0)
    blocks = [OcrBlock("orig", 20, 30, 300, 40, line_h=18, lines=2)]
    ov.set_blocks(blocks, ["译文一段"])
    assert len(ov._cards) == 1
    rect, text, px, bg, fg = ov._cards[0]
    assert text == "译文一段"
    assert rect.contains(20 + 5, 30 + 5)   # 卡片盖住原文位置
    assert px >= 11
    assert isinstance(bg, QColor) and isinstance(fg, QColor)
    ov.close()


def test_overlay_toggle_shows_original(qapp):
    """工具条"原文"开关：整体切回屏幕原文（不画卡片），再点切回译文。"""
    from PySide6.QtCore import QRect

    from ivyea_translate.ui.inplace_overlay import InPlaceOverlay

    ov = InPlaceOverlay(QRect(0, 0, 400, 200), _shot(qapp), 1.0)
    ov.set_blocks([OcrBlock("o", 20, 30, 300, 40, line_h=18)], ["译文"])
    assert not ov._show_original
    ov._toolbar.btn_orig.click()
    assert ov._show_original
    assert not ov.grab().isNull()   # 切换后 paintEvent 不崩
    ov._toolbar.btn_orig.click()
    assert not ov._show_original
    ov.close()


def test_overlay_reports_no_text(qapp):
    from PySide6.QtCore import QRect

    from ivyea_translate.ui.inplace_overlay import InPlaceOverlay

    ov = InPlaceOverlay(QRect(0, 0, 400, 200), _shot(qapp), 1.0)
    ov.set_blocks([], [])
    assert ov._status == "没有识别到文字"
    ov.close()


def test_font_shrinks_to_fit_small_block(qapp):
    """译文比原文长时字号自动收，绝不溢出到隔壁段。"""
    from ivyea_translate.ui.inplace_overlay import fit_font_px

    big = fit_font_px("短", 300, 40, 20)
    small = fit_font_px("这是一段非常非常长的译文" * 6, 300, 40, 20)
    assert big > small >= 11


# ---------- 必须关得掉（v0.25.0 的头号问题） ----------

def test_overlay_can_take_keyboard_focus(qapp):
    """置顶覆盖层若不接受焦点，Esc 根本到不了它 —— 用户就只能干瞪眼。"""
    from PySide6.QtCore import QRect, Qt

    from ivyea_translate.ui.inplace_overlay import InPlaceOverlay

    ov = InPlaceOverlay(QRect(0, 0, 400, 200), _shot(qapp), 1.0)
    assert ov.focusPolicy() == Qt.StrongFocus
    ov.close()


def test_escape_closes_overlay(qapp):
    from PySide6.QtCore import QEvent, QRect, Qt
    from PySide6.QtGui import QKeyEvent

    from ivyea_translate.ui.inplace_overlay import InPlaceOverlay

    ov = InPlaceOverlay(QRect(0, 0, 400, 200), _shot(qapp), 1.0)
    ov.set_blocks([OcrBlock("o", 20, 30, 300, 40, line_h=18)], ["译文"])
    seen = []
    ov.closed.connect(lambda: seen.append(1))
    ov.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    qapp.processEvents()
    assert seen, "Esc 必须能关掉覆盖层"


def test_overlay_has_visible_close_button(qapp):
    """光有快捷键不够：屏幕上必须看得见一个出口（工具条上的 ✕）。"""
    from PySide6.QtCore import QRect

    from ivyea_translate.ui.inplace_overlay import InPlaceOverlay

    ov = InPlaceOverlay(QRect(0, 0, 400, 200), _shot(qapp), 1.0)
    btn = ov._toolbar.btn_close
    assert btn.isEnabled()
    seen = []
    ov.closed.connect(lambda: seen.append(1))
    btn.click()
    qapp.processEvents()
    assert seen, "工具条 ✕ 必须能关掉覆盖层"


def test_overlay_window_wraps_region_plus_toolbar(qapp):
    """窗口 = 选区 + 一条工具条；选区本身的位置和大小必须原样保留。"""
    from PySide6.QtCore import QRect

    from ivyea_translate.ui.inplace_overlay import InPlaceOverlay, TOOLBAR_GAP

    region = QRect(120, 90, 400, 200)
    ov = InPlaceOverlay(region, _shot(qapp), 1.0)
    sel = ov._selection_rect()
    assert sel.size() == region.size()
    # 选区映回全局坐标要和原选区重合
    assert ov.geometry().x() + sel.x() == region.x()
    assert ov.geometry().y() + sel.y() == region.y()
    ext = ov._toolbar.height() + TOOLBAR_GAP
    assert ov.geometry().height() == region.height() + ext
    ov.close()


def test_overlay_widens_window_for_narrow_region(qapp):
    """选区比工具条窄时窗口向右拓宽，按钮不能被裁掉。"""
    from PySide6.QtCore import QRect

    from ivyea_translate.ui.inplace_overlay import InPlaceOverlay

    ov = InPlaceOverlay(QRect(50, 50, 80, 60), _shot(qapp, 80, 60), 1.0)
    assert ov.width() >= ov._toolbar.width()
    assert ov.rect().contains(ov._toolbar.geometry())
    ov.close()


def test_overlay_closes_on_window_deactivate(qapp):
    """切到别的窗口，覆盖层必须自己收走（bug：翻译一直赖在屏幕上）。"""
    from PySide6.QtCore import QEvent, QRect

    from ivyea_translate.ui.inplace_overlay import InPlaceOverlay

    ov = InPlaceOverlay(QRect(0, 0, 400, 200), _shot(qapp), 1.0)
    ov.show()
    seen = []
    ov.closed.connect(lambda: seen.append(1))
    ov.event(QEvent(QEvent.WindowDeactivate))
    qapp.processEvents()
    assert seen


def test_overlay_copy_buttons(qapp):
    """复制译文/复制原文走剪贴板；翻译完成前复制译文不可用。"""
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QGuiApplication

    from ivyea_translate.ui.inplace_overlay import InPlaceOverlay

    ov = InPlaceOverlay(QRect(0, 0, 400, 200), _shot(qapp), 1.0)
    assert not ov._toolbar.btn_copy_res.isEnabled()
    assert not ov._toolbar.btn_copy_src.isEnabled()
    ov.prepare([OcrBlock("hello", 20, 30, 300, 40, line_h=18),
                OcrBlock("world", 20, 120, 300, 40, line_h=18)])
    assert ov._toolbar.btn_copy_src.isEnabled()   # OCR 一到就能复制原文
    assert not ov._toolbar.btn_copy_res.isEnabled()
    ov.set_block_text(0, "你好")
    ov.set_block_text(1, "世界")
    ov.finish()
    assert ov._toolbar.btn_copy_res.isEnabled()
    ov._toolbar.btn_copy_res.click()
    assert QGuiApplication.clipboard().text() == "你好\n\n世界"
    ov._toolbar.btn_copy_src.click()
    assert QGuiApplication.clipboard().text() == "hello\n\nworld"
    ov.close()


def test_overlay_to_popup_carries_texts(qapp):
    """工具条"弹窗"：带着原文/译文和选区转对照弹窗。"""
    from PySide6.QtCore import QRect

    from ivyea_translate.ui.inplace_overlay import InPlaceOverlay

    region = QRect(30, 40, 400, 200)
    ov = InPlaceOverlay(region, _shot(qapp), 1.0)
    ov.set_blocks([OcrBlock("source", 20, 30, 300, 40, line_h=18)], ["译文"])
    got = []
    ov.popup_requested.connect(lambda s, r, rc: got.append((s, r, rc)))
    ov._toolbar.btn_popup.click()
    assert got == [("source", "译文", region)]
    ov.close()


# ---------- 卡片排版 ----------

def test_layout_card_does_not_widen_pointlessly(qapp):
    """短短一行译文不该把卡片撑到原文之外（差的那点高度是内边距造成的）。"""
    from PySide6.QtCore import QRect

    from ivyea_translate.ui.inplace_overlay import layout_card

    base = QRect(10, 10, 200, 22)
    bounds = QRect(0, 0, 800, 400)
    rect, px = layout_card("好", base, bounds, 14)
    assert rect.width() == base.width()


def test_layout_card_widens_before_shrinking_font(qapp):
    """译文放不下时先加宽，别一上来就把字压小。"""
    from PySide6.QtCore import QRect

    from ivyea_translate.ui.inplace_overlay import MIN_FONT_PX, layout_card

    base = QRect(10, 10, 120, 24)
    bounds = QRect(0, 0, 800, 400)
    rect, px = layout_card("这是一句比原文长不少的中文译文内容", base, bounds, 14)
    assert rect.width() > base.width()
    assert px > MIN_FONT_PX


def test_layout_card_stays_inside_bounds(qapp):
    """再放不下也不能长到窗口外面去。"""
    from PySide6.QtCore import QRect

    from ivyea_translate.ui.inplace_overlay import layout_card

    bounds = QRect(0, 0, 300, 120)
    rect, px = layout_card("很长的译文内容" * 20, QRect(10, 10, 200, 30), bounds, 16)
    assert rect.right() <= bounds.right() and rect.bottom() <= bounds.bottom()


def test_progressive_block_fill(qapp):
    """逐块翻译：翻好一块显示一块，没翻到的先不画。"""
    from PySide6.QtCore import QRect

    from ivyea_translate.ui.inplace_overlay import InPlaceOverlay

    ov = InPlaceOverlay(QRect(0, 0, 400, 200), _shot(qapp), 1.0)
    ov.prepare([OcrBlock("a", 10, 10, 200, 20, line_h=16),
                OcrBlock("b", 10, 100, 200, 20, line_h=16)])
    assert ov._cards == [None, None]
    ov.set_block_text(1, "第二段译文")
    assert ov._cards[0] is None and ov._cards[1] is not None
    ov.set_block_text(0, "第一段译文")
    assert all(c is not None for c in ov._cards)
    ov.close()


# ---------- 贴回原图：取样底色 + 自动黑白文字 ----------

def _image_with_center_text(qapp, edge="#3A6EA5", w=200, h=60):
    """纯色底 + 中心一坨"文字"色块：验证取样只采边缘环带。"""
    from PySide6.QtCore import QRect as _QRect
    from PySide6.QtGui import QColor, QImage, QPainter

    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(QColor(edge))
    p = QPainter(img)
    p.fillRect(_QRect(20, 15, w - 40, h - 30), QColor("#000000"))
    p.end()
    return img


def test_sample_bg_color_reads_edge_not_center(qapp):
    from PySide6.QtCore import QRect

    from ivyea_translate.ui.inplace_overlay import sample_bg_color

    img = _image_with_center_text(qapp)
    c = sample_bg_color(img, QRect(0, 0, 200, 60))
    # 中心大片黑"文字"不应把底色拉黑：结果要贴近边缘的蓝
    assert abs(c.red() - 0x3A) < 30
    assert abs(c.blue() - 0xA5) < 40


def test_sample_bg_color_clamps_out_of_bounds(qapp):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QColor, QImage

    from ivyea_translate.ui.inplace_overlay import sample_bg_color

    img = QImage(50, 50, QImage.Format_RGB32)
    img.fill(QColor("#FF0000"))
    assert sample_bg_color(img, QRect(-20, -20, 60, 60)).red() == 255
    # 完全在图外：退回白色而不是崩
    c = sample_bg_color(img, QRect(500, 500, 40, 40))
    assert (c.red(), c.green(), c.blue()) == (255, 255, 255)


@pytest.mark.parametrize("bg_hex, expect_dark_ink", [
    ("#FFFFFF", True),   # 白底 -> 黑字
    ("#F2F6EC", True),
    ("#1E1E1E", False),  # 深底 -> 白字
    ("#3A6EA5", False),
])
def test_ink_for_picks_contrast_color(qapp, bg_hex, expect_dark_ink):
    from PySide6.QtGui import QColor

    from ivyea_translate.ui.inplace_overlay import INK_DARK, INK_LIGHT, ink_for

    ink = ink_for(QColor(bg_hex))
    assert ink.name().upper() == (INK_DARK if expect_dark_ink else INK_LIGHT).upper()


def test_overlay_paints_without_crash_after_fill(qapp):
    """离屏跑一遍完整 paintEvent（卡片+外框+角标+状态），别等 Windows 上才炸。"""
    from PySide6.QtCore import QRect

    from ivyea_translate.ui.inplace_overlay import InPlaceOverlay

    ov = InPlaceOverlay(QRect(0, 0, 400, 200), _shot(qapp), 1.0)
    ov.set_blocks([OcrBlock("o", 20, 30, 300, 40, line_h=18)], ["译文"])
    assert not ov.grab().isNull()
    ov.close()
