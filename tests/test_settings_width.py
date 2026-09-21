"""设置页在最小窗宽下不能超宽：超宽 = 右侧被裁掉（横向滚动已关）。

v0.35.1 是长报错文案撑宽，v0.35.3 是外观那排多塞了个复选框撑宽——同一个坑踩两次，
所以这里按窗口允许的最小宽度直接量每一页内容的最小宽度。
"""
import sys

import pytest
from PySide6.QtWidgets import QScrollArea


@pytest.mark.parametrize("width", [600, 650, 920])
def test_every_page_fits_viewport_width(qapp, tmp_path, width, monkeypatch):
    from ivyea_translate.config import Config
    from ivyea_translate.ui.main_window import MainWindow

    # Windows 专属控件（系统标题栏复选框）也要参与量宽
    monkeypatch.setattr(sys, "platform", "win32")
    win = MainWindow(Config(tmp_path / "c.json"))
    win.resize(width, 700)
    win.show()
    qapp.processEvents()
    for i in range(win.tabs.count()):
        win.tabs.setCurrentIndex(i)
        qapp.processEvents()
        cur = win.tabs.currentWidget()
        areas = [cur] if isinstance(cur, QScrollArea) else cur.findChildren(QScrollArea)
        if not areas:
            continue  # 历史页是列表控件，自己管滚动
        for sa in areas:
            if sa.widget() is None:
                continue
            page = sa.widget()
            viewport_w = sa.viewport().width()
            need = page.minimumSizeHint().width()
            assert need <= viewport_w, (
                f"页 {win.tabs.tabText(i)} 在窗宽 {width} 下内容最小宽 {need} > 视口 {viewport_w}，右侧会被裁")
            assert page.width() <= viewport_w
    win.really_quit = True
    win.close()
