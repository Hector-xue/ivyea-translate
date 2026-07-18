"""翻译结果弹窗：无边框、置顶、可拖动、流式刷新、智能定位。

compute_popup_pos 是纯函数（可单测）：给定弹窗尺寸、锚区域（不可覆盖）、屏幕可用区，
返回弹窗左上角坐标。优先锚区正下方，放不下依次尝试上方、右侧、左侧，
都放不下则夹回屏幕内（此时允许重叠，属极端情况）。
"""
from __future__ import annotations

from typing import Tuple

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QTextOption
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import theme

Rect = Tuple[int, int, int, int]  # x, y, w, h


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(v, hi))


def compute_popup_pos(popup_w: int, popup_h: int, anchor: Rect, screen: Rect, margin: int = 12) -> Tuple[int, int]:
    ax, ay, aw, ah = anchor
    sx, sy, sw, sh = screen
    # 水平首选：与锚区居中对齐，夹回屏幕
    cx = _clamp(ax + (aw - popup_w) // 2, sx + margin, sx + sw - popup_w - margin)

    below_y = ay + ah + margin
    if below_y + popup_h <= sy + sh - margin:
        return cx, below_y

    above_y = ay - margin - popup_h
    if above_y >= sy + margin:
        return cx, above_y

    cy = _clamp(ay + (ah - popup_h) // 2, sy + margin, sy + sh - popup_h - margin)
    right_x = ax + aw + margin
    if right_x + popup_w <= sx + sw - margin:
        return right_x, cy

    left_x = ax - margin - popup_w
    if left_x >= sx + margin:
        return left_x, cy

    # 四个方向都放不下（锚区几乎占满屏），夹回屏幕内兜底
    return (
        _clamp(ax, sx + margin, max(sx + margin, sx + sw - popup_w - margin)),
        _clamp(below_y, sy + margin, max(sy + margin, sy + sh - popup_h - margin)),
    )


class _AutoGrowText(QPlainTextEdit):
    """只读文本区，随内容自动长高，超过 max_h 出滚动条。"""

    def __init__(self, max_h: int = 260, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFrameStyle(QFrame.NoFrame)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._max_h = max_h
        self.textChanged.connect(self._adjust_height)
        self._adjust_height()

    def _adjust_height(self):
        doc_h = int(self.document().size().height()) + 16
        self.setFixedHeight(max(40, min(doc_h, self._max_h)))


class TranslationPopup(QWidget):
    """一次翻译一个弹窗实例；关闭即销毁。支持钉住（失焦不关）。"""

    def __init__(self, original: str = "", show_original: bool = False, width: int = 420):
        super().__init__(None)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._pinned = False
        self._drag_offset: QPoint | None = None
        self._popup_width = width

        card = QWidget(self)
        card.setObjectName("PopupCard")
        card.setStyleSheet(
            f"""
            QWidget#PopupCard {{
                background: {theme.POPUP_BG};
                border: 1px solid rgba(255, 255, 255, 0.9);
                border-radius: 16px;
            }}
            QPlainTextEdit {{
                background: transparent;
                border: none;
                padding: 0;
                font-size: 14px;
            }}
            """
        )
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(36)
        shadow.setOffset(0, 6)
        shadow.setColor(Qt.GlobalColor.gray)
        card.setGraphicsEffect(shadow)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 14, 20, 24)  # 给阴影留边
        outer.addWidget(card)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 10, 16, 14)
        lay.setSpacing(8)

        # 标题行：状态 + 钉住 + 复制 + 关闭（整行也是拖动把手）
        head = QHBoxLayout()
        self.status_label = QLabel("翻译中…")
        self.status_label.setObjectName("Hint")
        head.addWidget(self.status_label)
        head.addStretch(1)
        self.pin_btn = QPushButton("📌")
        self.pin_btn.setObjectName("Ghost")
        self.pin_btn.setFixedSize(26, 26)
        self.pin_btn.setToolTip("钉住（失焦不关闭）")
        self.pin_btn.clicked.connect(self._toggle_pin)
        self.copy_btn = QPushButton("复制")
        self.copy_btn.setObjectName("Ghost")
        self.copy_btn.clicked.connect(self._copy_result)
        close_btn = QPushButton("✕")
        close_btn.setObjectName("Ghost")
        close_btn.setFixedSize(26, 26)
        close_btn.clicked.connect(self.close)
        head.addWidget(self.pin_btn)
        head.addWidget(self.copy_btn)
        head.addWidget(close_btn)
        lay.addLayout(head)

        # 原文区（截图翻译展示 OCR 结果，可折叠）
        self.original_text = original
        self._orig_view: _AutoGrowText | None = None
        if show_original and original:
            self._orig_toggle = QPushButton("原文 ▾")
            self._orig_toggle.setObjectName("Ghost")
            self._orig_toggle.setStyleSheet("text-align: left; font-size: 12px;")
            self._orig_toggle.clicked.connect(self._toggle_original)
            lay.addWidget(self._orig_toggle)
            self._orig_view = _AutoGrowText(max_h=140)
            self._orig_view.setPlainText(original)
            self._orig_view.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 13px;")
            lay.addWidget(self._orig_view)
            divider = QFrame()
            divider.setFrameShape(QFrame.HLine)
            divider.setStyleSheet("background: rgba(154,154,181,0.25); max-height: 1px; border: none;")
            lay.addWidget(divider)

        # 译文区（流式）
        self.result_view = _AutoGrowText(max_h=320)
        lay.addWidget(self.result_view)

        self.setFixedWidth(width)
        self._result_parts: list = []

    # ---- 流式接口（连接 TranslateWorker 信号） ----

    def append_chunk(self, piece: str) -> None:
        self._result_parts.append(piece)
        self.result_view.setPlainText("".join(self._result_parts))
        sb = self.result_view.verticalScrollBar()
        sb.setValue(sb.maximum())
        self.adjustSize()

    def set_done(self, full_text: str) -> None:
        self.result_view.setPlainText(full_text)
        self._result_parts = [full_text]
        self.status_label.setText("已翻译")
        self.adjustSize()

    def set_failed(self, message: str) -> None:
        self.status_label.setText("失败")
        self.result_view.setPlainText(message)
        self.result_view.setStyleSheet(f"color: {theme.ACCENT};")
        self.adjustSize()

    # ---- 定位 ----

    def show_near(self, anchor_rect: QRect) -> None:
        """在锚区域附近弹出（不覆盖锚区域）。锚可以是光标点（w=h=1）或截图框。"""
        self.adjustSize()
        screen = QGuiApplication.screenAt(anchor_rect.center()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x, y = compute_popup_pos(
            self.width(),
            self.height(),
            (anchor_rect.x(), anchor_rect.y(), anchor_rect.width(), anchor_rect.height()),
            (geo.x(), geo.y(), geo.width(), geo.height()),
        )
        self.move(x, y)
        self.show()

    def show_at_cursor(self) -> None:
        pos = QGuiApplication.primaryScreen().availableGeometry().center()
        try:
            from PySide6.QtGui import QCursor

            pos = QCursor.pos()
        except Exception:
            pass
        # 光标下方 = "原文下方"：锚区取光标处一个小点
        self.show_near(QRect(pos.x(), pos.y(), 1, 12))

    # ---- 交互 ----

    def _toggle_pin(self) -> None:
        self._pinned = not self._pinned
        self.pin_btn.setStyleSheet(f"color: {theme.ACCENT};" if self._pinned else "")

    def _toggle_original(self) -> None:
        if self._orig_view is None:
            return
        visible = self._orig_view.isVisible()
        self._orig_view.setVisible(not visible)
        self._orig_toggle.setText("原文 ▸" if visible else "原文 ▾")
        self.adjustSize()

    def _copy_result(self) -> None:
        text = self.result_view.toPlainText()
        if text:
            app = QApplication.instance()
            if app and hasattr(app, "mark_own_copy"):
                app.mark_own_copy(text)
            QGuiApplication.clipboard().setText(text)
            self.copy_btn.setText("已复制")
            QTimer.singleShot(1200, lambda: self.copy_btn.setText("复制"))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)
