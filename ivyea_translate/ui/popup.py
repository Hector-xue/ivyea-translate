"""翻译结果弹窗：无边框、置顶、可拖动、可调大小、流式刷新、智能定位。

compute_popup_pos 是纯函数（可单测）：给定弹窗尺寸、锚区域（不可覆盖）、屏幕可用区，
返回弹窗左上角坐标。优先锚区正下方，放不下依次尝试上方、右侧、左侧，
都放不下则夹回屏幕内（此时允许重叠，属极端情况）。

截图翻译两段式：先秒出"正在识别文字…"弹窗（set_status），OCR 完成后
set_original 回填原文并开始翻译，消除等待黑箱感。
"""
from __future__ import annotations

from typing import Optional, Tuple

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QTextOption
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import theme

Rect = Tuple[int, int, int, int]  # x, y, w, h

MAX_SIZE = 16777215
# 弹窗阴影留边（也是拖拽调整大小的抓取带）
MARGIN_L, MARGIN_T, MARGIN_R, MARGIN_B = 20, 14, 20, 24
RESIZE_GRAB = 12          # 距卡片边缘多少像素内算"抓边"
MIN_W, MIN_H = 320, 180


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


class _AutoGrowText(QTextEdit):
    """只读文本区，随内容自动长高，超过 max_h 出滚动条；
    用户手动调整弹窗大小后切换为自由伸展（set_free）。

    用 QTextEdit 而非 QPlainTextEdit：后者的 document().size() 不反映
    换行后的真实高度（永远约等于一行），会导致弹窗永远过矮。"""

    def __init__(self, max_h: int = 460, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFrameStyle(QFrame.NoFrame)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._max_h = max_h
        self._free = False
        self.textChanged.connect(self._adjust_height)

    def set_free(self) -> None:
        self._free = True
        self.setMinimumHeight(48)
        self.setMaximumHeight(MAX_SIZE)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._adjust_height()  # 宽度变化后按新换行重算高度

    def _adjust_height(self):
        if self._free:
            return
        w = self.viewport().width()
        if w <= 0:
            return  # 尚未布局，show/resize 后会再触发
        doc = self.document()
        doc.setTextWidth(w)  # 关键：按实际宽度换行后再量高度，否则长文本被低估
        target = max(60, min(int(doc.size().height()) + 14, self._max_h))
        if self.height() != target:
            self.setFixedHeight(target)


class TranslationPopup(QWidget):
    """一次翻译一个弹窗实例；关闭即销毁。支持钉住（失焦不关）。"""

    explain_requested = Signal()  # 首次点击"详解"时发出，由 app 接管生成

    def __init__(self, original: str = "", show_original: bool = False,
                 width: int = 520, show_explain: bool = False):
        super().__init__(None)
        self._show_explain = show_explain
        self._explain_started = False
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self._pinned = False
        self._manual_size = False
        self._drag_offset: Optional[QPoint] = None
        self._resize_edge = ""
        self._resize_start_geom: Optional[QRect] = None
        self._resize_start_global: Optional[QPoint] = None
        self._popup_width = width
        # 译文/原文区默认高度上限随屏幕自适应，长文本也能一眼浏览
        try:
            avail_h = QGuiApplication.primaryScreen().availableGeometry().height()
        except Exception:
            avail_h = 900
        self._res_max_h = int(min(avail_h * 0.62, 680))
        self._orig_max_h = int(min(avail_h * 0.32, 300))

        card = QWidget(self)
        card.setObjectName("PopupCard")
        card.setStyleSheet(
            f"""
            QWidget#PopupCard {{
                background: {theme.POPUP_BG};
                border: 1px solid rgba(255, 255, 255, 0.9);
                border-radius: 16px;
            }}
            QTextEdit {{
                background: transparent;
                border: none;
                padding: 0;
                font-size: 15px;
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
        lay.setContentsMargins(18, 12, 18, 8)
        lay.setSpacing(8)

        # 标题行：状态 + 钉住 + 复制 + 关闭（整行也是拖动把手）
        head = QHBoxLayout()
        self.status_label = QLabel("翻译中…")
        self.status_label.setObjectName("Hint")
        head.addWidget(self.status_label)
        head.addStretch(1)
        self.explain_btn = QPushButton("详解")
        self.explain_btn.setObjectName("Ghost")
        self.explain_btn.setToolTip("讲解重点词、语法与更地道的说法（需大模型）")
        self.explain_btn.clicked.connect(self._on_explain_clicked)
        self.explain_btn.setVisible(self._show_explain)
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
        head.addWidget(self.explain_btn)
        head.addWidget(self.pin_btn)
        head.addWidget(self.copy_btn)
        head.addWidget(close_btn)
        lay.addLayout(head)

        # 原文区（截图翻译展示 OCR 结果，可折叠；可先建后填 set_original）
        self.original_text = original
        self._orig_view: Optional[_AutoGrowText] = None
        self._orig_toggle: Optional[QPushButton] = None
        self._divider: Optional[QFrame] = None
        if show_original:
            self._orig_toggle = QPushButton("原文 ▾")
            self._orig_toggle.setObjectName("Ghost")
            self._orig_toggle.setStyleSheet("text-align: left; font-size: 12px;")
            self._orig_toggle.clicked.connect(self._toggle_original)
            lay.addWidget(self._orig_toggle)
            self._orig_view = _AutoGrowText(max_h=self._orig_max_h)
            self._orig_view.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 13px;")
            lay.addWidget(self._orig_view)
            self._divider = QFrame()
            self._divider.setFrameShape(QFrame.HLine)
            self._divider.setStyleSheet("background: rgba(147,163,136,0.3); max-height: 1px; border: none;")
            lay.addWidget(self._divider)
            if original:
                self._orig_view.setPlainText(original)
            else:
                self._set_original_visible(False)

        # 译文区（流式）
        self.result_view = _AutoGrowText(max_h=self._res_max_h)
        lay.addWidget(self.result_view, 1)

        # 详解区（默认隐藏，点"详解"按需生成）
        self._explain_divider = QFrame()
        self._explain_divider.setFrameShape(QFrame.HLine)
        self._explain_divider.setStyleSheet("background: rgba(147,163,136,0.3); max-height: 1px; border: none;")
        lay.addWidget(self._explain_divider)
        self._explain_label = QLabel("详解")
        self._explain_label.setObjectName("Hint")
        lay.addWidget(self._explain_label)
        self._explain_view = _AutoGrowText(max_h=int(self._res_max_h * 0.8))
        self._explain_view.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 13px;")
        lay.addWidget(self._explain_view)
        for w in (self._explain_divider, self._explain_label, self._explain_view):
            w.setVisible(False)
        self._explain_parts: list = []

        # 拖拽提示（贴右下角，暗示可从边缘缩放）
        tip = QLabel("拖动边缘可调整大小", card)
        tip.setObjectName("Hint")
        tip.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 11px;")
        tip.setAlignment(Qt.AlignRight)
        lay.addWidget(tip)
        self._resize_tip = tip

        self.setFixedWidth(width)
        self._result_parts: list = []

    # ---- 两段式（截图翻译）：先"识别中"，OCR 完成后回填原文 ----

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def set_original(self, text: str) -> None:
        self.original_text = text
        if self._orig_view is not None:
            self._orig_view.setPlainText(text)
            self._set_original_visible(True)
        self._relayout()

    def _set_original_visible(self, visible: bool) -> None:
        for w in (self._orig_toggle, self._orig_view, self._divider):
            if w is not None:
                w.setVisible(visible)

    # ---- 流式接口（连接 TranslateWorker 信号） ----

    def append_chunk(self, piece: str) -> None:
        self._result_parts.append(piece)
        self.result_view.setPlainText("".join(self._result_parts))
        sb = self.result_view.verticalScrollBar()
        sb.setValue(sb.maximum())
        self._relayout()

    def set_done(self, full_text: str) -> None:
        self.result_view.setPlainText(full_text)
        self._result_parts = [full_text]
        self.status_label.setText("已翻译")
        self._relayout()

    def set_failed(self, message: str) -> None:
        self.status_label.setText("失败")
        self.result_view.setPlainText(message)
        self.result_view.setStyleSheet(f"color: {theme.ACCENT};")
        self._relayout()

    # ---- 详解模式 ----

    def _on_explain_clicked(self) -> None:
        if not self._explain_started:
            # 首次：请求 app 生成
            if not self.result_view.toPlainText().strip():
                return
            self._explain_started = True
            self._set_explain_visible(True)
            self._explain_view.setPlainText("详解生成中…")
            self.explain_requested.emit()
            self._relayout()
        else:
            # 已有：折叠/展开
            vis = self._explain_view.isVisible()
            self._set_explain_visible(not vis)
            self._relayout()

    def _set_explain_visible(self, visible: bool) -> None:
        for w in (self._explain_divider, self._explain_label, self._explain_view):
            w.setVisible(visible)

    def set_explain_status(self, text: str) -> None:
        self._set_explain_visible(True)
        self._explain_view.setPlainText(text)
        self._relayout()

    def append_explain_chunk(self, piece: str) -> None:
        self._explain_parts.append(piece)
        self._explain_view.setPlainText("".join(self._explain_parts))
        self._relayout()

    def set_explain_done(self, full_text: str) -> None:
        self._explain_parts = [full_text]
        self._explain_view.setPlainText(full_text)
        self._relayout()

    def set_explain_failed(self, message: str) -> None:
        self._explain_view.setStyleSheet(f"color: {theme.ACCENT}; font-size: 13px;")
        self._explain_view.setPlainText(message)
        self._relayout()

    def _relayout(self) -> None:
        if not self._manual_size:
            self.adjustSize()

    def showEvent(self, event):
        super().showEvent(event)
        # 布局完成后再量一次：此时文本区宽度已确定，能按换行算出正确高度
        QTimer.singleShot(0, self._relayout)

    # ---- 手动调整大小 ----

    def enter_manual_size(self) -> None:
        if self._manual_size:
            return
        self._manual_size = True
        # 解除固定宽/高，允许四向自由拖拽
        self.setMinimumSize(MIN_W, MIN_H)
        self.setMaximumSize(MAX_SIZE, MAX_SIZE)
        self.result_view.set_free()
        if self._orig_view is not None:
            self._orig_view.set_free()

    # ---- 边缘拖拽缩放（无边框窗口自绘） ----

    def _card_rect(self) -> QRect:
        return QRect(MARGIN_L, MARGIN_T,
                     self.width() - MARGIN_L - MARGIN_R,
                     self.height() - MARGIN_T - MARGIN_B)

    def _edge_at(self, pos: QPoint) -> str:
        r = self._card_rect()
        g = RESIZE_GRAB
        # 需落在卡片纵/横跨度内（含抓取带）才算对应边
        in_x = r.left() - g <= pos.x() <= r.right() + g
        in_y = r.top() - g <= pos.y() <= r.bottom() + g
        left = in_y and abs(pos.x() - r.left()) <= g
        right = in_y and abs(pos.x() - r.right()) <= g
        top = in_x and abs(pos.y() - r.top()) <= g
        bottom = in_x and abs(pos.y() - r.bottom()) <= g
        return ("top" if top else "") + ("bottom" if bottom else "") + \
               ("left" if left else "") + ("right" if right else "")

    _CURSORS = {
        "left": Qt.SizeHorCursor, "right": Qt.SizeHorCursor,
        "top": Qt.SizeVerCursor, "bottom": Qt.SizeVerCursor,
        "topleft": Qt.SizeFDiagCursor, "bottomright": Qt.SizeFDiagCursor,
        "topright": Qt.SizeBDiagCursor, "bottomleft": Qt.SizeBDiagCursor,
    }

    def _apply_resize(self, gp: QPoint) -> None:
        d = gp - self._resize_start_global
        g = QRect(self._resize_start_geom)
        e = self._resize_edge
        if "left" in e:
            g.setLeft(min(g.left() + d.x(), g.right() - MIN_W))
        if "right" in e:
            g.setRight(max(g.right() + d.x(), g.left() + MIN_W))
        if "top" in e:
            g.setTop(min(g.top() + d.y(), g.bottom() - MIN_H))
        if "bottom" in e:
            g.setBottom(max(g.bottom() + d.y(), g.top() + MIN_H))
        self.setGeometry(g)

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
        if self._orig_toggle is not None:
            self._orig_toggle.setText("原文 ▸" if visible else "原文 ▾")
        self._relayout()

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
        if event.button() != Qt.LeftButton:
            return
        edge = self._edge_at(event.position().toPoint())
        if edge:
            # 从边缘按下 -> 缩放
            self.enter_manual_size()
            self._resize_edge = edge
            self._resize_start_geom = QRect(self.geometry())
            self._resize_start_global = event.globalPosition().toPoint()
        else:
            # 其余区域 -> 移动
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        event.accept()

    def mouseMoveEvent(self, event):
        if self._resize_edge and (event.buttons() & Qt.LeftButton):
            self._apply_resize(event.globalPosition().toPoint())
            event.accept()
            return
        if self._drag_offset is not None and (event.buttons() & Qt.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        # 无按键：靠近边缘时给出缩放光标提示
        edge = self._edge_at(event.position().toPoint())
        self.setCursor(self._CURSORS.get(edge, Qt.ArrowCursor))

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        self._resize_edge = ""
        self._resize_start_geom = None
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)
