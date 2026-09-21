"""Windows 原生消息诊断/自愈的纯逻辑。"""
from ivyea_translate.ui import winshell as ws


def test_should_resync_only_on_shell_show_while_qt_thinks_minimized():
    assert ws.should_resync(ws.WM_SHOWWINDOW, 1, qt_minimized=True, native_iconic=False)
    assert not ws.should_resync(ws.WM_SHOWWINDOW, 1, qt_minimized=False, native_iconic=False)
    assert not ws.should_resync(ws.WM_SHOWWINDOW, 1, qt_minimized=True, native_iconic=True)
    assert not ws.should_resync(ws.WM_SHOWWINDOW, 0, qt_minimized=True, native_iconic=False)
    assert not ws.should_resync(ws.WM_SIZE, 1, qt_minimized=True, native_iconic=False)


def test_describe_style_names_bits():
    text = ws.describe_style(0x80000000 | 0x00020000 | 0x00080000, 0x00080000)
    assert "WS_POPUP" in text and "WS_MINIMIZEBOX" in text and "WS_SYSMENU" in text
    assert "WS_EX_LAYERED" in text and "WS_CAPTION" not in text


def test_describe_event_filters_noise():
    assert ws.describe_event(ws.WM_SHOWWINDOW, 1, 4) == "WM_SHOWWINDOW show=1 原因=SW_OTHERUNZOOM"
    assert ws.describe_event(ws.WM_SIZE, 1, 0) == "WM_SIZE MINIMIZED"
    assert ws.describe_event(ws.WM_SYSCOMMAND, 0xF020 | 0x3, 0) == "WM_SYSCOMMAND SC_MINIMIZE"
    assert ws.describe_event(ws.WM_SYSCOMMAND, 0xF090, 0) == ""      # SC_MOUSEMENU 之类不记
    assert ws.describe_event(0x0200, 0, 0) == ""                       # WM_MOUSEMOVE 不记
