"""全局热键：pynput GlobalHotKeys 封装。

pynput 回调在后台线程，这里只 emit Qt Signal（Qt 跨线程信号自动排队到主线程），
绝不在回调里直接碰 UI。
"""
from __future__ import annotations

import sys
from typing import Dict, Optional

from PySide6.QtCore import QObject, Signal

_WINDOWS = sys.platform == "win32"


class HotkeyManager(QObject):
    select_translate = Signal()
    screenshot_translate = Signal()
    show_main_window = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._listener = None
        self.last_error: Optional[str] = None

    def start(self, mapping: Dict[str, str]) -> bool:
        """mapping: {"select_translate": "<ctrl>+<alt>+t", ...}，pynput 语法。

        返回是否注册成功；失败原因在 last_error（headless/无权限环境下优雅降级）。
        """
        self.stop()
        try:
            from pynput import keyboard
        except Exception as e:
            self.last_error = f"pynput 不可用：{e}"
            return False

        signal_map = {
            "select_translate": self.select_translate,
            "screenshot_translate": self.screenshot_translate,
            "show_main_window": self.show_main_window,
        }
        bindings = {}
        for name, combo in mapping.items():
            sig = signal_map.get(name)
            if sig is None or not combo:
                continue
            # 默认参数绑定当前 sig，避免闭包晚绑定
            bindings[combo] = (lambda s=sig: s.emit())
        if not bindings:
            self.last_error = "没有可注册的热键"
            return False
        try:
            self._listener = keyboard.GlobalHotKeys(bindings)
            self._listener.daemon = True
            self._listener.start()
            self.last_error = None
            return True
        except Exception as e:
            self._listener = None
            self.last_error = f"热键注册失败：{e}"
            return False

    def restart(self, mapping: Dict[str, str]) -> bool:
        return self.start(mapping)

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
