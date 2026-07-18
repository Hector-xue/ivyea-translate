"""剪贴板监听（复制翻译）：QClipboard.dataChanged -> 过滤 -> 触发翻译。

过滤逻辑抽成纯函数 should_translate（可单测）：
 - 空/纯空白不翻
 - 与上一次内容相同不翻（很多程序复制会触发多次 dataChanged）
 - 超长不翻（防止误复制整页文档打爆 token）
 - 我们自己写回剪贴板的内容不翻（防自触发）
"""
from __future__ import annotations

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


class ClipboardWatcher(QObject):
    """enabled 时监听系统剪贴板，命中过滤规则则 emit text_copied。"""

    text_copied = Signal(str)

    def __init__(self, max_chars: int = 3000, parent=None):
        super().__init__(parent)
        self._enabled = False
        self._paused = False
        self._last_text: Optional[str] = None
        self._own_text: Optional[str] = None
        self.max_chars = max_chars
        QGuiApplication.clipboard().dataChanged.connect(self._on_changed)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, on: bool) -> None:
        self._enabled = on
        if on:
            # 开启瞬间把当前剪贴板记为已见，避免立刻翻译旧内容
            self._last_text = QGuiApplication.clipboard().text() or None

    def set_paused(self, paused: bool) -> None:
        """取词流程期间暂停（selection.get_selected_text 会调用）。"""
        self._paused = paused
        if not paused:
            self._last_text = QGuiApplication.clipboard().text() or None

    def mark_own_copy(self, text: str) -> None:
        """记录我们主动写入剪贴板的内容（如'复制译文'按钮），下次变化跳过。"""
        self._own_text = text

    def _on_changed(self) -> None:
        if not self._enabled or self._paused:
            return
        text = QGuiApplication.clipboard().text()
        if should_translate(text, self._last_text, self._own_text, self.max_chars):
            self._last_text = text
            self.text_copied.emit(text)
        elif text:
            self._last_text = text
