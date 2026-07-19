"""剪贴板监听：复制翻译（自动弹窗）+ 双击 Ctrl+C 触发划词翻译。

过滤逻辑抽成纯函数 should_translate / is_double_copy（可单测）。
双击 Ctrl+C：短时间内两次复制**相同文本** -> 触发翻译。此路径完全不注入
按键（文本已经在剪贴板里），是最可靠的划词触发方式。
"""
from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QGuiApplication


def should_translate(
    text: Optional[str],
    last_text: Optional[str],
    own_text: Optional[str],
    max_chars: int,
) -> bool:
    if not text or not text.strip():
        return False
    if text == last_text:
        return False
    if own_text is not None and text == own_text:
        return False
    if max_chars > 0 and len(text) > max_chars:
        return False
    return True


def is_double_copy(
    text: Optional[str],
    prev_text: Optional[str],
    now_s: float,
    prev_time_s: float,
    window_s: float,
) -> bool:
    """两次复制同一段非空文本且间隔在窗口内 = 双击 Ctrl+C。"""
    if not text or not text.strip() or text != prev_text:
        return False
    return 0 < now_s - prev_time_s <= window_s


class ClipboardWatcher(QObject):
    """监听系统剪贴板。

    - text_copied：复制翻译开启时，新文本复制即发
    - double_copied：双击 Ctrl+C（两次同文本）时发（独立于复制翻译开关）
    """

    text_copied = Signal(str)
    double_copied = Signal(str)

    def __init__(self, max_chars: int = 3000, parent=None):
        super().__init__(parent)
        self._enabled = False
        self._paused = False
        self._last_text: Optional[str] = None
        self._own_text: Optional[str] = None
        self._prev_copy_text: Optional[str] = None
        self._prev_copy_time = 0.0
        self.max_chars = max_chars
        self.double_copy_enabled = True
        self.double_window_s = 0.7
        QGuiApplication.clipboard().dataChanged.connect(self._on_changed)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, on: bool) -> None:
        self._enabled = on
        if on:
            self._last_text = QGuiApplication.clipboard().text() or None

    def set_paused(self, paused: bool) -> None:
        """取词流程期间暂停（selection.get_selected_text 会调用）。"""
        self._paused = paused
        if not paused:
            self._last_text = QGuiApplication.clipboard().text() or None

    def mark_own_copy(self, text: str) -> None:
        self._own_text = text

    def _on_changed(self) -> None:
        if self._paused:
            return
        text = QGuiApplication.clipboard().text()
        now = time.monotonic()

        # 双击 Ctrl+C 检测（独立于复制翻译开关；自家写入的不算）
        if self.double_copy_enabled and text != self._own_text:
            if is_double_copy(text, self._prev_copy_text, now, self._prev_copy_time, self.double_window_s):
                self._prev_copy_time = 0.0  # 防三连击重复触发
                self._last_text = text
                self.double_copied.emit(text)
                return
        self._prev_copy_text = text
        self._prev_copy_time = now

        if not self._enabled:
            return
        if should_translate(text, self._last_text, self._own_text, self.max_chars):
            self._last_text = text
            self.text_copied.emit(text)
        elif text:
            self._last_text = text
