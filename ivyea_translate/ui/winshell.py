"""Windows 原生窗口消息侧的诊断与自愈（仅 Windows 生效，其余平台全是空操作）。

背景：主窗是无边框 + 分层透明（WA_TranslucentBackground）的 WS_POPUP 窗口。用户
反馈"显示桌面后点开别的软件，主窗自己冒出来，且标题栏三个按钮点不动"。v0.35.0
先补了 WS_MINIMIZEBOX（Qt 对 Frameless 不自动补），用户实测**仍在**——所以这里
不再猜，把原生消息记进日志，同时做一条不依赖根因的自愈：

Qt 收到 WM_SIZE(SIZE_MINIMIZED) 会把窗口标成 Minimized 并 handleHidden()（停止
渲染）。若之后 shell 直接 ShowWindow 把窗口带回来而**没有**伴随 SIZE_RESTORED，
Qt 仍认为它最小化、不再重绘：屏幕上是分层窗口残留的旧像素，而分层窗口按像素
alpha 命中——旧像素/空像素点上去直接穿透，看起来就是"按钮按不动"。
should_resync() 判定这种"系统说显示、Qt 说最小化"的错位，宿主据此把 Qt 状态
掰回来（setWindowState 去掉 Minimized），窗口立刻恢复重绘与响应。
"""
from __future__ import annotations

import ctypes
import logging
import sys

log = logging.getLogger(__name__)

_WINDOWS = sys.platform == "win32"

WM_SIZE = 0x0005
WM_ACTIVATE = 0x0006
WM_SHOWWINDOW = 0x0018
WM_ACTIVATEAPP = 0x001C
WM_SYSCOMMAND = 0x0112

_SHOW_REASON = {0: "程序调用", 1: "SW_PARENTCLOSING", 2: "SW_OTHERZOOM",
                3: "SW_PARENTOPENING", 4: "SW_OTHERUNZOOM"}
_SIZE_KIND = {0: "RESTORED", 1: "MINIMIZED", 2: "MAXIMIZED", 3: "MAXSHOW", 4: "MAXHIDE"}

GWL_STYLE = -16
GWL_EXSTYLE = -20
_STYLE_BITS = {
    0x80000000: "WS_POPUP", 0x00C00000: "WS_CAPTION", 0x00080000: "WS_SYSMENU",
    0x00040000: "WS_THICKFRAME", 0x00020000: "WS_MINIMIZEBOX", 0x00010000: "WS_MAXIMIZEBOX",
    0x10000000: "WS_VISIBLE", 0x20000000: "WS_MINIMIZE", 0x01000000: "WS_MAXIMIZE",
}
_EXSTYLE_BITS = {0x00080000: "WS_EX_LAYERED", 0x00000080: "WS_EX_TOOLWINDOW",
                 0x00040000: "WS_EX_APPWINDOW", 0x08000000: "WS_EX_NOACTIVATE"}


def describe_style(style: int, exstyle: int) -> str:
    """纯函数：把样式位翻成可读名字（日志用）。"""
    names = [n for bit, n in _STYLE_BITS.items() if style & bit == bit]
    names += [n for bit, n in _EXSTYLE_BITS.items() if exstyle & bit]
    return f"0x{style:08X}/0x{exstyle:08X} " + " ".join(names)


def should_resync(msg: int, wparam: int, qt_minimized: bool, native_iconic: bool) -> bool:
    """纯函数：系统把窗口显示出来了（WM_SHOWWINDOW, wParam=TRUE），而 Qt 仍认为
    它最小化、原生也不再是图标态 → 状态错位，需要把 Qt 掰回来。"""
    return msg == WM_SHOWWINDOW and bool(wparam) and qt_minimized and not native_iconic


class _MSG(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint),
                ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t),
                ("time", ctypes.c_uint), ("pt_x", ctypes.c_long), ("pt_y", ctypes.c_long)]


def read_msg(message) -> tuple:
    """PySide 的 nativeEvent 给的是 VoidPtr：按 MSG 结构读出 (hwnd, msg, wParam, lParam)。

    hwnd 必须从消息里取，**nativeEvent 里绝不能调 self.winId()**：窗口创建期间
    （CreateWindowEx 内部）系统就同步发 WM_NCCALCSIZE 等消息进来，此时 Qt 还没登记
    句柄，winId() 会再次触发建窗 → 又发消息 → 又进 nativeEvent，无限递归直到栈溢出，
    进程以 0xC000041D 静默崩溃（v0.36.0/v0.37.0 双击无反应的根因）。"""
    m = _MSG.from_address(int(message))
    return int(m.hwnd or 0), int(m.message), int(m.wParam), int(m.lParam)


def log_style(hwnd: int, tag: str) -> None:
    if not _WINDOWS or not hwnd:
        return
    try:
        u = ctypes.windll.user32
        style = u.GetWindowLongW(ctypes.c_void_p(hwnd), GWL_STYLE) & 0xFFFFFFFF
        ex = u.GetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE) & 0xFFFFFFFF
        log.info("窗口样式[%s]：%s", tag, describe_style(style, ex))
    except Exception as e:  # 诊断代码绝不许把窗口搞崩
        log.info("窗口样式读取失败：%s", e)


def is_iconic(hwnd: int) -> bool:
    if not _WINDOWS or not hwnd:
        return False
    try:
        return bool(ctypes.windll.user32.IsIconic(ctypes.c_void_p(hwnd)))
    except Exception:
        return False


def describe_event(msg: int, wparam: int, lparam: int) -> str:
    """纯函数：值得记日志的窗口消息 -> 文案；不关心的返回空串。"""
    if msg == WM_SHOWWINDOW:
        return f"WM_SHOWWINDOW show={int(bool(wparam))} 原因={_SHOW_REASON.get(lparam, lparam)}"
    if msg == WM_SIZE:
        return f"WM_SIZE {_SIZE_KIND.get(wparam, wparam)}"
    if msg == WM_SYSCOMMAND:
        cmd = wparam & 0xFFF0
        names = {0xF020: "SC_MINIMIZE", 0xF030: "SC_MAXIMIZE", 0xF120: "SC_RESTORE", 0xF060: "SC_CLOSE"}
        return f"WM_SYSCOMMAND {names.get(cmd, hex(cmd))}" if cmd in names else ""
    if msg == WM_ACTIVATEAPP:
        return f"WM_ACTIVATEAPP active={int(bool(wparam))}"
    return ""


# ---------- 原生外壳：无边框但保留系统窗口的全部行为 ----------
#
# 用户对照实验（v0.35.3/0.35.4）：勾"使用系统标题栏"后"显示桌面后跟着还原""三键点不动"
# 全消失，只有无边框分层窗口有这毛病，别的程序都没有。业界成熟做法（VS Code/Chromium/
# qwindowkit）不是去掉系统边框，而是**保留 WS_CAPTION|WS_THICKFRAME 这些样式，只在
# WM_NCCALCSIZE 里把非客户区算成 0**：外壳（任务栏、显示桌面、贴边、动画）眼里它就是
# 一个普通窗口，而屏幕上一个像素的系统边框都没有。同时不再用 WA_TranslucentBackground
# 的分层窗口（UpdateLayeredWindow）：分层窗口按像素 alpha 命中、内容是整张位图上传，
# 是"点不动"这类怪事的温床。圆角与投影交给 DWM（Win11 圆角 8px，Win10 直角），
# 自绘的投影留白在这个模式下收掉。

WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
WM_NCACTIVATE = 0x0086
HTCLIENT = 1

WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
SWP_FRAMECHANGED = 0x0020
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SM_CXSIZEFRAME = 32
SM_CYSIZEFRAME = 33
SM_CXPADDEDBORDER = 92
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2


def native_chrome_style(style: int) -> int:
    """纯函数：在现有样式上补齐"普通窗口"该有的位。WS_POPUP 保留不动（Qt 建的）。"""
    return style | WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX


def maximized_inset(frame_x: int, frame_y: int, zoomed: bool) -> tuple:
    """纯函数：WM_NCCALCSIZE 里客户区要往里收多少。

    最大化时 Windows 会把窗口矩形往屏幕外撑出一圈边框宽度（本意是把边框藏到屏幕外），
    我们把非客户区算成 0 后这圈就变成了被屏幕裁掉的内容——必须按边框宽度收回来。
    非最大化时 0：客户区 = 整个窗口矩形。
    """
    if not zoomed:
        return (0, 0, 0, 0)
    return (frame_x, frame_y, frame_x, frame_y)


class _MARGINS(ctypes.Structure):
    _fields_ = [("left", ctypes.c_int), ("right", ctypes.c_int),
                ("top", ctypes.c_int), ("bottom", ctypes.c_int)]


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def install_native_chrome(hwnd: int) -> bool:
    """给 Qt 建好的无边框窗口补上系统窗口样式 + DWM 投影/圆角。失败返回 False（保持原样）。"""
    if not _WINDOWS or not hwnd:
        return False
    try:
        u = ctypes.windll.user32
        h = ctypes.c_void_p(hwnd)
        style = u.GetWindowLongW(h, GWL_STYLE) & 0xFFFFFFFF
        u.SetWindowLongW(h, GWL_STYLE, ctypes.c_int(native_chrome_style(style) & 0xFFFFFFFF).value)
        # 让 DWM 认为窗口"有边框"：往客户区延伸 1px 边框，投影与 Win11 圆角就会出现，
        # 而 1px 会被我们自己的内容盖住
        try:
            dwm = ctypes.windll.dwmapi
            dwm.DwmExtendFrameIntoClientArea(h, ctypes.byref(_MARGINS(1, 1, 1, 1)))
            pref = ctypes.c_int(DWMWCP_ROUND)
            dwm.DwmSetWindowAttribute(h, DWMWA_WINDOW_CORNER_PREFERENCE,
                                      ctypes.byref(pref), ctypes.sizeof(pref))
        except Exception as e:
            log.info("DWM 投影/圆角设置失败（不影响使用）：%s", e)
        # SWP_FRAMECHANGED 触发一次 WM_NCCALCSIZE(TRUE)，Qt 会据此把边框留白记成 0
        u.SetWindowPos(h, None, 0, 0, 0, 0,
                       SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
        log_style(hwnd, "原生外壳")
        return True
    except Exception as e:
        log.warning("原生外壳安装失败，保持纯无边框：%s", e)
        return False


def frame_thickness(hwnd: int) -> tuple:
    """当前 DPI 下系统边框厚度 (x, y)。"""
    u = ctypes.windll.user32
    try:
        dpi = u.GetDpiForWindow(ctypes.c_void_p(hwnd)) or 96
        fx = u.GetSystemMetricsForDpi(SM_CXSIZEFRAME, dpi) + u.GetSystemMetricsForDpi(SM_CXPADDEDBORDER, dpi)
        fy = u.GetSystemMetricsForDpi(SM_CYSIZEFRAME, dpi) + u.GetSystemMetricsForDpi(SM_CXPADDEDBORDER, dpi)
    except Exception:
        fx = u.GetSystemMetrics(SM_CXSIZEFRAME) + u.GetSystemMetrics(SM_CXPADDEDBORDER)
        fy = u.GetSystemMetrics(SM_CYSIZEFRAME) + u.GetSystemMetrics(SM_CXPADDEDBORDER)
    return int(fx), int(fy)


def handle_nccalcsize(hwnd: int, wparam: int, lparam: int) -> int:
    """WM_NCCALCSIZE：非客户区 = 0；最大化时按边框厚度把客户区收回屏幕内。返回 0。"""
    if wparam:
        u = ctypes.windll.user32
        zoomed = bool(u.IsZoomed(ctypes.c_void_p(hwnd)))
        fx, fy = frame_thickness(hwnd) if zoomed else (0, 0)
        l, t, r, b = maximized_inset(fx, fy, zoomed)
        rc = _RECT.from_address(lparam)   # NCCALCSIZE_PARAMS.rgrc[0] 就在结构开头
        rc.left += l
        rc.top += t
        rc.right -= r
        rc.bottom -= b
    return 0


def def_window_proc(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
    u = ctypes.windll.user32
    u.DefWindowProcW.restype = ctypes.c_ssize_t
    u.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    return int(u.DefWindowProcW(hwnd, msg, wparam, lparam))
