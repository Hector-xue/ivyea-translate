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


def test_edge_resize_band(qapp, tmp_path):
    """贴边 8px 内识别为缩放边；中间区域不该被误判（否则点卡片就在缩放）。"""
    from PySide6.QtCore import QPoint

    win = _make_window(qapp, tmp_path)
    win.resize(800, 600)
    if not win._frameless:
        pytest.skip("原生窗口由系统负责缩放")
    assert win._edge_at(QPoint(2, 300)) == Qt.LeftEdge
    assert win._edge_at(QPoint(798, 300)) == Qt.RightEdge
    assert win._edge_at(QPoint(2, 2)) == (Qt.LeftEdge | Qt.TopEdge)
    assert not win._edge_at(QPoint(400, 300))  # 窗口中间：不是缩放边


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
