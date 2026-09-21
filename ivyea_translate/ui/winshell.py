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
    """PySide 的 nativeEvent 给的是 VoidPtr：按 MSG 结构读出 (msg, wParam, lParam)。"""
    m = _MSG.from_address(int(message))
    return int(m.message), int(m.wParam), int(m.lParam)


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
