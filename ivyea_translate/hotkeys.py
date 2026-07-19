"""全局热键。

Windows：原生 RegisterHotKey + 专用消息循环线程（ctypes）。
 - 这是 Windows 全局热键的正规做法：无钩子、无管理员依赖、
   与其他程序冲突时逐条明确报错（pynput 的键盘钩子在打包后的
   环境不可靠，且失败无感知）。
其他平台：pynput GlobalHotKeys 兜底（开发机用）。

parse_hotkey 是纯函数（可单测）：把 "<ctrl>+<alt>+t" 解析成
(修饰键位掩码, 虚拟键码)。

回调都在后台线程，只 emit Qt Signal（自动排队到主线程）。
"""
from __future__ import annotations

import logging
import sys
import threading
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

_WINDOWS = sys.platform == "win32"

# Win32 修饰键掩码（RegisterHotKey）
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

_MOD_NAMES = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "cmd": MOD_WIN,
    "super": MOD_WIN,
}

_NAMED_VK = {
    "space": 0x20,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "enter": 0x0D,
    "return": 0x0D,
    "backspace": 0x08,
    "delete": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "page_up": 0x21,
    "page_down": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "`": 0xC0,
    "-": 0xBD,
    "=": 0xBB,
    "[": 0xDB,
    "]": 0xDD,
    ";": 0xBA,
    "'": 0xDE,
    ",": 0xBC,
    ".": 0xBE,
    "/": 0xBF,
    "\\": 0xDC,
}


def parse_hotkey(combo: str) -> Tuple[int, int]:
    """把 pynput 风格组合键（如 "<ctrl>+<alt>+t"）解析为 (mods, vk)。

    抛 ValueError 表示格式非法或没有主键。
    """
    mods = 0
    vk: Optional[int] = None
    parts = [p.strip() for p in combo.split("+") if p.strip()]
    if not parts:
        raise ValueError("空的快捷键")
    for part in parts:
        name = part.lower().strip("<>").strip()
        if not name:
            raise ValueError(f"非法快捷键片段：{part!r}")
        if name in _MOD_NAMES:
            mods |= _MOD_NAMES[name]
        elif len(name) == 1 and (name.isascii() and (name.isalpha() or name.isdigit())):
            if vk is not None:
                raise ValueError(f"只能有一个主键：{combo!r}")
            vk = ord(name.upper())
        elif name.startswith("f") and name[1:].isdigit() and 1 <= int(name[1:]) <= 24:
            if vk is not None:
                raise ValueError(f"只能有一个主键：{combo!r}")
            vk = 0x70 + int(name[1:]) - 1
        elif name in _NAMED_VK:
            if vk is not None:
                raise ValueError(f"只能有一个主键：{combo!r}")
            vk = _NAMED_VK[name]
        else:
            raise ValueError(f"不认识的按键：{part!r}")
    if vk is None:
        raise ValueError(f"缺少主键（只有修饰键）：{combo!r}")
    return mods, vk


class HotkeyManager(QObject):
    select_translate = Signal()
    screenshot_translate = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.last_error: Optional[str] = None
        self._pynput_listener = None
        self._win_thread: Optional[threading.Thread] = None
        self._win_tid: Optional[int] = None

    def _signal_map(self) -> Dict[str, Signal]:
        return {
            "select_translate": self.select_translate,
            "screenshot_translate": self.screenshot_translate,
        }

    def start(self, mapping: Dict[str, str]) -> bool:
        """注册热键。返回是否全部成功；部分/全部失败的原因在 last_error。"""
        self.stop()
        entries = [
            (name, combo)
            for name, combo in mapping.items()
            if name in self._signal_map() and combo
        ]
        if not entries:
            self.last_error = "没有可注册的热键"
            return False
        if _WINDOWS:
            return self._start_windows(entries)
        return self._start_pynput(entries)

    def restart(self, mapping: Dict[str, str]) -> bool:
        return self.start(mapping)

    def stop(self) -> None:
        if self._pynput_listener is not None:
            try:
                self._pynput_listener.stop()
            except Exception:
                pass
            self._pynput_listener = None
        if _WINDOWS and self._win_thread is not None and self._win_tid:
            import ctypes

            ctypes.windll.user32.PostThreadMessageW(self._win_tid, WM_QUIT, 0, 0)
            self._win_thread.join(timeout=2)
            self._win_thread = None
            self._win_tid = None

    # ---------- Windows：RegisterHotKey ----------

    def _start_windows(self, entries: List[Tuple[str, str]]) -> bool:
        parsed: List[Tuple[str, int, int]] = []
        errors: List[str] = []
        for name, combo in entries:
            try:
                mods, vk = parse_hotkey(combo)
                parsed.append((name, mods, vk))
            except ValueError as e:
                errors.append(str(e))
        started = threading.Event()
        result: Dict[str, List[str]] = {"errors": errors}

        def loop():
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            self._win_tid = kernel32.GetCurrentThreadId()
            registered: Dict[int, str] = {}
            for idx, (name, mods, vk) in enumerate(parsed, start=1):
                if user32.RegisterHotKey(None, idx, mods | MOD_NOREPEAT, vk):
                    registered[idx] = name
                else:
                    err = ctypes.get_last_error() or kernel32.GetLastError()
                    result["errors"].append(
                        f"{dict(entries).get(name, name)} 注册失败"
                        f"（错误码 {err}，多为已被其他程序占用）"
                    )
            started.set()
            sig_map = self._signal_map()
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    name = registered.get(msg.wParam)
                    if name:
                        log.info("热键触发：%s", name)
                        sig = sig_map.get(name)
                        if sig:
                            sig.emit()
            for idx in registered:
                user32.UnregisterHotKey(None, idx)

        self._win_thread = threading.Thread(target=loop, daemon=True, name="hotkey-loop")
        self._win_thread.start()
        started.wait(timeout=3)
        if result["errors"]:
            self.last_error = "；".join(result["errors"])
            log.warning("热键注册问题：%s", self.last_error)
            return False
        self.last_error = None
        log.info("热键全部注册成功：%s", [c for _, c in entries])
        return True

    # ---------- 非 Windows：pynput 兜底 ----------

    def _start_pynput(self, entries: List[Tuple[str, str]]) -> bool:
        try:
            from pynput import keyboard
        except Exception as e:
            self.last_error = f"pynput 不可用：{e}"
            return False
        sig_map = self._signal_map()
        bindings = {}
        for name, combo in entries:
            sig = sig_map.get(name)
            if sig is not None:
                bindings[combo] = (lambda s=sig: s.emit())
        try:
            self._pynput_listener = keyboard.GlobalHotKeys(bindings)
            self._pynput_listener.daemon = True
            self._pynput_listener.start()
            self.last_error = None
            return True
        except Exception as e:
            self._pynput_listener = None
            self.last_error = f"热键注册失败：{e}"
            log.warning("%s", self.last_error)
            return False
