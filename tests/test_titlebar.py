"""自绘标题栏 / 无边框窗口回归测试。

系统标题栏去掉后，最小化/最大化/关闭全靠自绘按钮：关闭按钮必须仍然走
closeEvent 的"藏到托盘"，否则用户点 ✕ 会直接把程序按死在后台无窗口状态。
"""
import sys

import pytest
from PySide6.QtCore import Qt


def _make_window(qapp, tmp_path):
    from ivyea_translate.config import Config
    from ivyea_translate.ui.main_window import MainWindow

    cfg = Config(tmp_path / "config.json")
    return MainWindow(cfg)


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS 保留原生窗口（红绿灯）")
def test_window_is_frameless(qapp, tmp_path):
    win = _make_window(qapp, tmp_path)
    assert win._frameless is True
    assert win.windowFlags() & Qt.FramelessWindowHint
    assert win.titlebar.min_btn is not None
    assert win.titlebar.max_btn is not None
    assert win.titlebar.close_btn is not None


def test_head_status_alias_still_works(qapp, tmp_path):
    """app / 其它模块通过 head_status 写状态文案，改标题栏后不能断。"""
    win = _make_window(qapp, tmp_path)
    win.head_status.setText("测试中…")
    assert win.titlebar.status.text() == "测试中…"


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS 保留原生窗口（红绿灯）")
def test_close_button_hides_to_tray(qapp, tmp_path):
    win = _make_window(qapp, tmp_path)
    win.show()
    win.titlebar.close_btn.click()
    assert win.isHidden()        # 只是藏起来
    assert win.really_quit is False


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS 保留原生窗口（红绿灯）")
def test_max_button_toggles_and_syncs_glyph(qapp, tmp_path):
    win = _make_window(qapp, tmp_path)
    win.show()
    before = win.titlebar.max_btn.text()
    win.titlebar.toggle_max_restore()
    qapp.processEvents()
    assert win.isMaximized()
    assert win.titlebar.max_btn.text() != before  # □ -> ❐
    win.titlebar.toggle_max_restore()
    qapp.processEvents()
    assert not win.isMaximized()


def test_edge_resize_band_covers_all_four_sides(qapp, tmp_path):
    """四条边都要能抓。

    v0.22.0 只能左右拖：上边被标题栏、下边被内容区把鼠标事件吃掉了。现在四周
    留出投影带（那里没有子控件），上下左右必须都判定为缩放边。
    """
    from PySide6.QtCore import QPoint

    win = _make_window(qapp, tmp_path)
    win.resize(800, 600)
    if not win._frameless:
        pytest.skip("原生窗口由系统负责缩放")
    assert win._edge_at(QPoint(2, 300)) == Qt.LeftEdge
    assert win._edge_at(QPoint(798, 300)) == Qt.RightEdge
    assert win._edge_at(QPoint(400, 2)) == Qt.TopEdge
    assert win._edge_at(QPoint(400, 598)) == Qt.BottomEdge
    assert win._edge_at(QPoint(2, 2)) == (Qt.LeftEdge | Qt.TopEdge)
    assert win._edge_at(QPoint(798, 598)) == (Qt.RightEdge | Qt.BottomEdge)
    assert not win._edge_at(QPoint(400, 300))  # 窗口中间：不是缩放边


def test_titlebar_does_not_eat_top_resize_band(qapp, tmp_path):
    """标题栏必须整体落在抓边带之下，否则窗口上边缘永远拖不动。"""
    from ivyea_translate.ui import titlebar

    win = _make_window(qapp, tmp_path)
    if not win._frameless:
        pytest.skip("原生窗口由系统负责缩放")
    win.show()
    qapp.processEvents()
    top_of_titlebar = win.titlebar.mapTo(win, win.titlebar.rect().topLeft()).y()
    assert top_of_titlebar > titlebar.RESIZE_BAND


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS 保留原生窗口（红绿灯）")
def test_maximize_drops_shadow_margin_and_radius(qapp, tmp_path):
    """最大化时投影留白与圆角必须收掉，否则四角会露出桌面、边上有透明条。"""
    from ivyea_translate.ui import titlebar

    win = _make_window(qapp, tmp_path)
    win.show()
    qapp.processEvents()
    assert win._root_layout.contentsMargins().left() == titlebar.SHADOW_MARGIN
    assert win.shell.styleSheet() == ""

    win.showMaximized()
    qapp.processEvents()
    assert win._root_layout.contentsMargins().left() == 0
    assert "border-radius: 0" in win.shell.styleSheet()

    win.showNormal()
    qapp.processEvents()
    assert win._root_layout.contentsMargins().left() == titlebar.SHADOW_MARGIN
    assert win.shell.styleSheet() == ""


def test_result_view_grows_with_window(qapp, tmp_path):
    """译文区跟着窗口长：窗口拉高后不该在底部留一片死白。"""
    win = _make_window(qapp, tmp_path)
    win.resize(760, 620)
    win.show()
    qapp.processEvents()
    qapp.processEvents()
    short = win.result_view.height()
    win.resize(760, 900)
    qapp.processEvents()
    qapp.processEvents()
    assert win.result_view.height() > short


def test_settings_hints_align_with_field_text(qapp, tmp_path):
    """浅色说明必须和它解释的控件里的文字左对齐（曾经差 13px 两头不靠）。"""
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QLabel

    from ivyea_translate.ui import theme

    win = _make_window(qapp, tmp_path)
    win.resize(820, 780)
    win.show()
    win.tabs.setCurrentIndex(3)
    qapp.processEvents()
    qapp.processEvents()

    hints = [w for w in win.findChildren(QLabel) if w.objectName() == "FieldHint"]
    assert len(hints) == 3
    combo_text_x = win.engine_combo.mapTo(win, QPoint(0, 0)).x() + theme.FIELD_TEXT_INSET
    hint_text_x = hints[0].mapTo(win, QPoint(0, 0)).x() + theme.FIELD_TEXT_INSET
    assert hint_text_x == combo_text_x
