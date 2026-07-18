"""closeEvent 行为回归测试。

背景：closeEvent 里 ignore()（关窗到托盘）会拒掉 QEvent::Quit 触发的
closeAllWindows，导致 app.quit() 被取消、程序退不掉。really_quit 标志
必须放行 close，托盘退出走 request_quit() 先置位再 quit。
"""


def _make_window(qapp, tmp_path):
    from ivyea_translate.config import Config
    from ivyea_translate.ui.main_window import MainWindow

    cfg = Config(tmp_path / "config.json")
    return MainWindow(cfg)


def test_close_hides_instead_of_closing(qapp, tmp_path):
    win = _make_window(qapp, tmp_path)
    win.show()
    closed = win.close()
    assert closed is False  # close 被 ignore
    assert win.isHidden()   # 但窗口藏起来了


def test_really_quit_allows_close(qapp, tmp_path):
    win = _make_window(qapp, tmp_path)
    win.show()
    win.really_quit = True
    assert win.close() is True
