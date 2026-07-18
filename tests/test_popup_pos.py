import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ivyea_translate.ui.popup import compute_popup_pos

SCREEN = (0, 0, 1920, 1080)
MARGIN = 12


def test_prefers_below_anchor():
    anchor = (500, 300, 400, 200)  # 底边 y=500
    x, y = compute_popup_pos(420, 300, anchor, SCREEN)
    assert y == 500 + MARGIN
    # 水平居中于锚区
    assert x == 500 + (400 - 420) // 2


def test_flips_above_when_no_room_below():
    anchor = (500, 700, 400, 300)  # 底边 y=1000，下方只剩 80
    x, y = compute_popup_pos(420, 300, anchor, SCREEN)
    assert y == 700 - MARGIN - 300  # 锚区上方


def test_side_placement_when_neither_above_nor_below():
    anchor = (500, 50, 400, 980)  # 竖着几乎占满屏
    x, y = compute_popup_pos(420, 300, anchor, SCREEN)
    assert x == 500 + 400 + MARGIN  # 右侧
    # 弹窗不与锚区垂直越界
    assert 0 <= y <= 1080 - 300


def test_left_placement_when_anchor_hugs_right_edge():
    anchor = (1400, 50, 508, 980)  # 右边贴屏，右侧放不下
    x, y = compute_popup_pos(420, 300, anchor, SCREEN)
    assert x == 1400 - MARGIN - 420  # 左侧


def test_fallback_clamps_into_screen():
    anchor = (0, 0, 1920, 1080)  # 全屏锚区，四向都放不下
    x, y = compute_popup_pos(420, 300, anchor, SCREEN)
    assert 0 <= x <= 1920 - 420
    assert 0 <= y <= 1080 - 300


def test_never_covers_anchor_in_normal_cases():
    anchor = (800, 400, 300, 150)
    w, h = 420, 260
    x, y = compute_popup_pos(w, h, anchor, SCREEN)
    ax, ay, aw, ah = anchor
    overlap_x = max(0, min(x + w, ax + aw) - max(x, ax))
    overlap_y = max(0, min(y + h, ay + ah) - max(y, ay))
    assert overlap_x == 0 or overlap_y == 0
