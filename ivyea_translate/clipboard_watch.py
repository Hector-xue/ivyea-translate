"""剪贴板监听：Ctrl+C+C 触发划词翻译。

is_double_copy 是纯函数（可单测）：短时间内两次复制**相同非空文本** -> 触发。
此路径完全不注入按键（文本已经在剪贴板里），是最可靠的划词触发方式。
"""
from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QGuiApplication


def is_double_copy(
    text: Optional[str],
    prev_text: Optional[str],
    now_s: float,
    prev_time_s: float,
    window_s: float,
) -> bool:
    """两次复制同一段非空文本且间隔在窗口内 = Ctrl+C+C。"""
    if not text or not text.strip() or text != prev_text:
        return False
    return 0 < now_s - prev_time_s <= window_s


class ClipboardWatcher(QObject):
    """监听系统剪贴板，命中"Ctrl+C+C"手势时 emit double_copied。"""

    double_copied = Signal(str)

    def __init__(self, max_chars: int = 3000, parent=None):
        super().__init__(parent)
        self._own_text: Optional[str] = None
        self._prev_copy_text: Optional[str] = None
        self._prev_copy_time = 0.0
        self.max_chars = max_chars
        self.double_copy_enabled = True
        self.double_window_s = 0.7
        QGuiApplication.clipboard().dataChanged.connect(self._on_changed)

    def mark_own_copy(self, text: str) -> None:
        """记录我们自己写入剪贴板的内容（如'复制译文'），避免被计入双击。"""
        self._own_text = text

    def _on_changed(self) -> None:
        text = QGuiApplication.clipboard().text()
        now = time.monotonic()
        if self.double_copy_enabled and text and text != self._own_text:
            if is_double_copy(text, self._prev_copy_text, now, self._prev_copy_time, self.double_window_s):
                # 超长（误复制整页）不翻，避免打爆 token / 卡界面
                if self.max_chars > 0 and len(text) > self.max_chars:
                    self._prev_copy_text = text
                    self._prev_copy_time = now
                    return
                self._prev_copy_time = 0.0  # 防三连击重复触发
                self.double_copied.emit(text)
                return
        self._prev_copy_text = text
        self._prev_copy_time = now
