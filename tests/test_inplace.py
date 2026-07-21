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

    from ivyea_translate.ui.inplace_overlay import InPlaceOverlay

    ov = InPlaceOverlay(QRect(100, 80, 400, 200), _shot(qapp), 1.0)
    ov.resize(400, 200)
    blocks = [OcrBlock("orig", 20, 30, 300, 40, line_h=18, lines=2)]
    ov.set_blocks(blocks, ["译文一段"])
    assert len(ov._cards) == 1
    rect, text, px = ov._cards[0]
    assert text == "译文一段"
    assert rect.contains(20 + 5, 30 + 5)   # 卡片盖住原文位置
    assert px >= 11
    ov.close()


def test_overlay_hover_hides_that_card(qapp):
    """悬停某块 -> 该块不绘制，露出屏幕上的真实原文。"""
    from PySide6.QtCore import QPoint, QRect

    from ivyea_translate.ui.inplace_overlay import InPlaceOverlay

    ov = InPlaceOverlay(QRect(0, 0, 400, 200), _shot(qapp), 1.0)
    ov.resize(400, 200)
    ov.set_blocks([OcrBlock("o", 20, 30, 300, 40, line_h=18)], ["译文"])
    inside = ov._cards[0][0].center()
    assert ov._card_at(inside) == 0
    assert ov._card_at(QPoint(5, 195)) == -1
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
    """光有快捷键不够：屏幕上必须看得见一个出口。"""
    from PySide6.QtCore import QRect

    from ivyea_translate.ui.inplace_overlay import InPlaceOverlay

    ov = InPlaceOverlay(QRect(0, 0, 400, 200), _shot(qapp), 1.0)
    btn = ov._close_rect()
    assert btn.width() >= 18 and btn.height() >= 18
    assert ov.rect().contains(btn)          # 在窗口内，点得到
    assert btn.right() >= ov.width() - 40    # 贴着右上角
    ov.close()


def test_overlay_window_matches_region_exactly(qapp):
    """窗口不能比选区大：多出来的透明边会白白吃掉选区外的点击。"""
    from PySide6.QtCore import QRect

    from ivyea_translate.ui.inplace_overlay import InPlaceOverlay

    region = QRect(120, 90, 400, 200)
    ov = InPlaceOverlay(region, _shot(qapp), 1.0)
    assert ov.geometry().size() == region.size()
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
    ov.resize(400, 200)
    ov.prepare([OcrBlock("a", 10, 10, 200, 20, line_h=16),
                OcrBlock("b", 10, 100, 200, 20, line_h=16)])
    assert ov._cards == [None, None]
    ov.set_block_text(1, "第二段译文")
    assert ov._cards[0] is None and ov._cards[1] is not None
    ov.set_block_text(0, "第一段译文")
    assert all(c is not None for c in ov._cards)
    ov.close()
