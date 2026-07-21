"""原位翻译：坐标换算、段落切分、覆盖层行为。"""
import pytest

from ivyea_translate.ocr import OcrBlock, bounding_block
from ivyea_translate.translator import join_blocks, split_translation


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


# ---------- 段落切分 ----------

def test_split_translation_matches_block_count():
    assert split_translation("一\n\n二\n\n三", 3) == ["一", "二", "三"]


def test_split_translation_tolerates_extra_blank_lines():
    assert split_translation("一\n\n\n  \n\n二", 2) == ["一", "二"]


def test_split_translation_degrades_when_count_mismatch():
    """段数对不上就返回空，让调用方整段贴一张卡——绝不错位。"""
    assert split_translation("一\n\n二", 3) == []
    assert split_translation("只有一段", 2) == []


def test_split_translation_single_block_keeps_everything():
    assert split_translation("一\n\n二", 1) == ["一\n\n二"]


def test_join_blocks_skips_empty():
    assert join_blocks(["a", "  ", "b"]) == "a\n\nb"


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
    assert px >= 9
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
    assert big > small >= 9
