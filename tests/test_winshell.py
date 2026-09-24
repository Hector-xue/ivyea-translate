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


def test_native_chrome_style_adds_system_bits_keeps_popup():
    style = ws.native_chrome_style(0x80000000 | ws.WS_CAPTION)
    for bit in (ws.WS_THICKFRAME, ws.WS_SYSMENU, ws.WS_MINIMIZEBOX, ws.WS_MAXIMIZEBOX):
        assert style & bit == bit
    assert style & 0x80000000
    # 带 WS_CAPTION 系统会在失焦/缩放时往窗口上画老式标题栏（v0.37.2 拖动缩放出现"一圈窗口"）
    assert style & ws.WS_CAPTION == 0


def test_maximized_inset_only_when_zoomed():
    assert ws.maximized_inset(8, 8, zoomed=False) == (0, 0, 0, 0)
    assert ws.maximized_inset(8, 8, zoomed=True) == (8, 8, 8, 8)


def test_handle_nccalcsize_shrinks_rect_when_zoomed(monkeypatch):
    import ctypes

    rc = ws._RECT(-8, -8, 1928, 1088)
    monkeypatch.setattr(ws, "frame_thickness", lambda hwnd: (8, 8))

    class FakeUser32:
        def IsZoomed(self, h):
            return 1

    monkeypatch.setattr(ws.ctypes, "windll", type("W", (), {"user32": FakeUser32()})(), raising=False)
    assert ws.handle_nccalcsize(1, 1, ctypes.addressof(rc)) == 0
    assert (rc.left, rc.top, rc.right, rc.bottom) == (0, 0, 1920, 1080)
    # wParam=0 时不动矩形
    rc2 = ws._RECT(-8, -8, 1928, 1088)
    assert ws.handle_nccalcsize(1, 0, ctypes.addressof(rc2)) == 0
    assert (rc2.left, rc2.top) == (-8, -8)


def test_read_msg_returns_hwnd_from_the_message_itself():
    """nativeEvent 必须从 MSG 里取 hwnd：建窗期间调 winId() 会无限递归致进程崩溃（v0.36/0.37）。"""
    import ctypes

    m = ws._MSG(hwnd=0x1234, message=ws.WM_NCCALCSIZE, wParam=1, lParam=0x5678)
    assert ws.read_msg(ctypes.addressof(m)) == (0x1234, ws.WM_NCCALCSIZE, 1, 0x5678)


def test_native_event_never_calls_winid():
    import inspect

    from ivyea_translate.ui.main_window import MainWindow

    code = [ln.split("#")[0] for ln in inspect.getsource(MainWindow.nativeEvent).splitlines()]
    assert not any("winId(" in ln for ln in code)


def test_stylechanging_strips_layered_only_for_exstyle():
    """Qt 改透明度/flags 时会把 WS_EX_LAYERED 加回来；分层窗口正是"显示桌面后冒出来、按钮点不动"的温床。"""
    import ctypes

    ss = ws._STYLESTRUCT(styleOld=0x100, styleNew=0x100 | ws.WS_EX_LAYERED)
    addr = ctypes.addressof(ss)
    gwl_exstyle = ws.GWL_EXSTYLE & 0xFFFFFFFF          # WPARAM 里是无符号的 -20
    assert ws.strip_layered_on_stylechanging(ws.WM_STYLECHANGING, gwl_exstyle, addr)
    assert ss.styleNew == 0x100
    assert not ws.strip_layered_on_stylechanging(ws.WM_STYLECHANGING, gwl_exstyle, addr)  # 已干净
    other = ws._STYLESTRUCT(styleOld=0, styleNew=ws.WS_EX_LAYERED)
    assert not ws.strip_layered_on_stylechanging(ws.WM_STYLECHANGING, ws.GWL_STYLE & 0xFFFFFFFF,
                                                 ctypes.addressof(other))
    assert other.styleNew == ws.WS_EX_LAYERED
    assert not ws.strip_layered_on_stylechanging(ws.WM_SIZE, gwl_exstyle, addr)


def test_hit_test_edges_maps_bands_to_resize_codes():
    """缩放交给系统：抓边带报 HT* 码（Qt startSystemResize 在原生外壳下会卡住/画出老式边框）。"""
    rect = (100, 100, 900, 700)
    band = 16
    assert ws.hit_test_edges(500, 400, rect, band) == ws.HTCLIENT
    assert ws.hit_test_edges(105, 400, rect, band) == ws.HTLEFT
    assert ws.hit_test_edges(890, 400, rect, band) == ws.HTRIGHT
    assert ws.hit_test_edges(500, 110, rect, band) == ws.HTTOP
    assert ws.hit_test_edges(500, 690, rect, band) == ws.HTBOTTOM
    assert ws.hit_test_edges(101, 101, rect, band) == ws.HTTOPLEFT
    assert ws.hit_test_edges(899, 101, rect, band) == ws.HTTOPRIGHT
    assert ws.hit_test_edges(101, 699, rect, band) == ws.HTBOTTOMLEFT
    assert ws.hit_test_edges(899, 699, rect, band) == ws.HTBOTTOMRIGHT
    # 带宽边界：恰好在带外是客户区
    assert ws.hit_test_edges(116, 400, rect, band) == ws.HTCLIENT
    assert ws.hit_test_edges(883, 400, rect, band) == ws.HTCLIENT
