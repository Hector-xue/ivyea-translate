"""取当前选中文字：备份剪贴板 -> 模拟 Ctrl+C -> 等待剪贴板变化 -> 读取 -> 还原。

Windows 上用 GetClipboardSequenceNumber(ctypes) 精确判断"复制是否真的发生"，
避免固定 sleep 的竞态；其他平台退化为轮询内容变化。

注意：本模块运行在调用方指定的后台线程（不能阻塞 UI 线程），
剪贴板读写用 pynput/ctypes 与平台 API，不用 QClipboard（它要求主线程）。
"""
from __future__ import annotations

import ctypes
import sys
import time
from typing import Callable, Optional

_WINDOWS = sys.platform == "win32"

# 模拟 Ctrl+C 后等待剪贴板更新的超时（秒）。超时=没有选中文字。
COPY_TIMEOUT = 0.6
POLL_INTERVAL = 0.02


def _clipboard_seq() -> int:
    if _WINDOWS:
        return ctypes.windll.user32.GetClipboardSequenceNumber()
    return 0


def _read_clipboard_text() -> Optional[str]:
    """平台无关读剪贴板文本。Windows 走 Win32 API（避免额外依赖），其他平台走 Qt。"""
    if _WINDOWS:
        return _win_read_clipboard()
    return _qt_read_clipboard()


def _write_clipboard_text(text: str) -> None:
    if _WINDOWS:
        _win_write_clipboard(text)
    else:
        _qt_write_clipboard(text)


# ---------- Windows 剪贴板（ctypes，可在任意线程调用） ----------

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def _win_open_clipboard(retries: int = 10) -> bool:
    user32 = ctypes.windll.user32
    for _ in range(retries):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.01)
    return False


def _win_read_clipboard() -> Optional[str]:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    if not _win_open_clipboard():
        return None
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _win_write_clipboard(text: str) -> None:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    if not _win_open_clipboard():
        return
    try:
        user32.EmptyClipboard()
        data = text.encode("utf-16-le") + b"\x00\x00"
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            return
        ptr = kernel32.GlobalLock(handle)
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
    finally:
        user32.CloseClipboard()


# ---------- 非 Windows：Qt 剪贴板（仅供开发机降级，主线程外不保证） ----------

def _qt_read_clipboard() -> Optional[str]:
    try:
        from PySide6.QtGui import QGuiApplication

        cb = QGuiApplication.clipboard()
        return cb.text() or None
    except Exception:
        return None


def _qt_write_clipboard(text: str) -> None:
    try:
        from PySide6.QtGui import QGuiApplication

        QGuiApplication.clipboard().setText(text)
    except Exception:
        pass


def _send_ctrl_c() -> None:
    from pynput import keyboard

    kb = keyboard.Controller()
    # 先松开可能还按着的热键修饰键，避免 Ctrl+Alt+C 之类的组合被目标程序收到
    for key in (keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r, keyboard.Key.shift):
        try:
            kb.release(key)
        except Exception:
            pass
    with kb.pressed(keyboard.Key.ctrl):
        kb.press("c")
        kb.release("c")


def get_selected_text(pause_watch: Optional[Callable[[bool], None]] = None) -> Optional[str]:
    """返回当前前台程序中选中的文字；没有选中返回 None。

    pause_watch(True/False)：取词期间暂停剪贴板监听，防止自触发复制翻译。
    """
    if pause_watch:
        pause_watch(True)
    try:
        old_text = _read_clipboard_text()
        old_seq = _clipboard_seq()

        _send_ctrl_c()

        deadline = time.monotonic() + COPY_TIMEOUT
        new_text: Optional[str] = None
        while time.monotonic() < deadline:
            time.sleep(POLL_INTERVAL)
            if _WINDOWS:
                if _clipboard_seq() != old_seq:
                    new_text = _read_clipboard_text()
                    break
            else:
                current = _read_clipboard_text()
                if current is not None and current != old_text:
                    new_text = current
                    break

        # 还原用户剪贴板（只有真的被我们覆盖了才还原）
        if new_text is not None and old_text is not None and new_text != old_text:
            _write_clipboard_text(old_text)

        if new_text is None or not new_text.strip():
            return None
        return new_text
    finally:
        if pause_watch:
            pause_watch(False)
