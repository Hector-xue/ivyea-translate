"""应用内提示卡：欢迎引导、发现新版本、更新进度/失败都用它，替代系统 QMessageBox。

系统消息框是另一套视觉语言（白底、蓝色 i 图标、灰色按钮条），盖在照片背景的主窗上
像贴了张便签。这里不开新的顶层窗口，而是在主窗 Shell 里铺一层半透明遮罩、中间浮
一张与界面同源的卡片：同一套圆角、配色、按钮，跟着主题走，也不牵扯任何原生窗口
行为（显示桌面、DWM、分层窗口那些坑一个都不沾）。

用法（非阻塞，没有嵌套事件循环）：

    sheet = Sheet.show_on(host, "发现新版本", body, [("later", "以后", "ghost"),
                                                     ("now", "立即更新", "primary")])
    sheet.finished.connect(lambda key: ...)   # key = 按钮 key；Esc/点遮罩 = "dismiss"
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple, Union

from PySide6.QtCore import QEasingCurve, QEvent, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import theme

CARD_WIDTH = 440
CARD_RADIUS = 16
SHADOW = 14
DISMISS = "dismiss"

Button = Tuple[str, str, str]   # (key, 文案, 角色 primary/ghost/plain)


class Sheet(QWidget):
    """铺满宿主的遮罩 + 居中卡片。宿主一般是主窗的 Shell。"""

    finished = Signal(str)

    def __init__(self, host: QWidget, title: str, body: Union[str, QWidget, None],
                 buttons: Sequence[Button], dismissible: bool = True):
        super().__init__(host)
        self.setObjectName("SheetScrim")
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._dismissible = dismissible
        self._done = False
        self._t = 0.0
        self._buttons: dict = {}
        self._primary: Optional[QPushButton] = None

        self.card = QFrame(self)
        self.card.setObjectName("SheetCard")
        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(30, 26, 30, 24)
        lay.setSpacing(0)

        self.title = QLabel(title)
        self.title.setObjectName("SheetTitle")
        self.title.setWordWrap(True)
        lay.addWidget(self.title)
        lay.addSpacing(22)

        if isinstance(body, str):
            body = body_label(body)
        self.body = body
        if body is not None:
            lay.addWidget(body)

        self._btn_row = QHBoxLayout()
        self._btn_row.setContentsMargins(0, 26, 0, 0)
        self._btn_row.setSpacing(8)
        self._btn_row.addStretch(1)
        lay.addLayout(self._btn_row)
        self.set_buttons(buttons)

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(170)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self._fx: Optional[QGraphicsOpacityEffect] = None

        host.installEventFilter(self)
        self.setGeometry(host.rect())

    # ---------- 对外 ----------

    @classmethod
    def show_on(cls, host: QWidget, title: str, body: Union[str, QWidget, None],
                buttons: Sequence[Button], dismissible: bool = True) -> "Sheet":
        sheet = cls(host, title, body, buttons, dismissible)
        sheet.open()
        return sheet

    def open(self) -> None:
        self.setGeometry(self.parentWidget().rect())
        self._place_card()
        self.raise_()
        self.show()
        # 淡入时给卡片挂一个透明度效果，播完立刻摘掉：效果会把整棵子树离屏渲染，
        # 常驻会让卡片里的进度条等刷新变钝
        self._fx = QGraphicsOpacityEffect(self.card)
        self._fx.setOpacity(0.0)
        self.card.setGraphicsEffect(self._fx)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        (self._primary or self).setFocus()

    def close_with(self, key: str) -> None:
        if self._done:
            return
        self._done = True
        self.parentWidget().removeEventFilter(self)
        self.hide()
        self.finished.emit(key)
        self.deleteLater()

    def set_title(self, text: str) -> None:
        self.title.setText(text)

    def set_buttons(self, buttons: Sequence[Button]) -> None:
        for btn in self._buttons.values():
            self._btn_row.removeWidget(btn)
            btn.deleteLater()
        self._buttons.clear()
        self._primary = None
        for key, label, role in buttons:
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            if role == "primary":
                btn.setObjectName("Primary")
                btn.setDefault(True)
                self._primary = btn
            elif role == "ghost":
                btn.setObjectName("SheetGhost")
            btn.clicked.connect(lambda _=False, k=key: self.close_with(k))
            self._btn_row.addWidget(btn)
            self._buttons[key] = btn
        self._place_card()

    def button(self, key: str) -> Optional[QPushButton]:
        return self._buttons.get(key)

    # ---------- 布局与绘制 ----------

    def _on_anim(self, value) -> None:
        self._t = float(value)
        if self._fx is not None:
            self._fx.setOpacity(self._t)
            if self._t >= 1.0:
                self.card.setGraphicsEffect(None)
                self._fx = None
        self._place_card()
        self.update()

    def _place_card(self) -> None:
        # 宽度随宿主收窄（窄窗口下别探出去），高度按换行后的实际宽度重算
        w = min(CARD_WIDTH, max(240, self.width() - 32))
        self.card.setFixedWidth(w)
        lay = self.card.layout()
        h = lay.totalHeightForWidth(w) if lay.hasHeightForWidth() else lay.totalSizeHint().height()
        self.card.setFixedHeight(max(h, lay.totalMinimumSize().height()))
        x = (self.width() - w) // 2
        # 视觉中心略偏上；淡入时从下方 8px 浮上来
        y = max(16, int((self.height() - self.card.height()) * 0.42)) + int(round((1.0 - self._t) * 8))
        self.card.move(x, y)

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == QEvent.Resize:
            self.setGeometry(obj.rect())
            self._place_card()
        return False

    def resizeEvent(self, event):
        self._place_card()
        super().resizeEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        # 遮罩：沿用窗体圆角，最大化时宿主自己是直角，遮罩跟着宿主画满即可
        radius = _host_radius(self)
        scrim = QColor(0, 0, 0, int((0.34 if theme.IS_DARK else 0.20) * 255 * self._t))
        p.setPen(Qt.NoPen)
        p.setBrush(scrim)
        p.drawRoundedRect(QRectF(self.rect()), radius, radius)
        # 卡片投影：一圈逐层变淡的圆角描边，和主窗外壳同一个画法
        g = QRectF(self.card.geometry())
        p.setBrush(Qt.NoBrush)
        base = (theme.SHADOW_ALPHA + 20) * self._t
        for i in range(SHADOW, 0, -1):
            a = int(base * (1.0 - i / SHADOW) ** 2.2)
            if a <= 0:
                continue
            p.setPen(QPen(QColor(*theme.SHADOW_RGB, a), 1))
            r = g.adjusted(-i + 0.5, -i + 4.5, i - 0.5, i - 0.5)
            p.drawRoundedRect(r, CARD_RADIUS + i * 0.6, CARD_RADIUS + i * 0.6)
        p.end()

    # ---------- 交互 ----------

    def mousePressEvent(self, event):
        if not self.card.geometry().contains(event.position().toPoint()) and self._dismissible:
            self.close_with(DISMISS)
        event.accept()   # 遮罩吃掉点击，别漏到下面的界面

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            if self._dismissible:
                self.close_with(DISMISS)
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self._primary is not None:
            self._primary.click()
            return
        super().keyPressEvent(event)


def _host_radius(w: QWidget) -> float:
    win = w.window()
    rounded = getattr(win, "_shell_rounded", None)
    if callable(rounded) and not rounded():
        return 0.0
    return float(theme.WINDOW_RADIUS)


def body_label(text: str, muted: bool = False) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("SheetMuted" if muted else "SheetBody")
    lbl.setWordWrap(True)
    lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lbl


def keycaps(combo: str) -> QWidget:
    """"Ctrl + Alt + S" / "Ctrl+C+C" -> 一排键帽。"""
    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    for key in [k.strip() for k in combo.split("+") if k.strip()]:
        cap = QLabel(key)
        cap.setObjectName("Keycap")
        cap.setAlignment(Qt.AlignCenter)
        row.addWidget(cap)
    row.addStretch(1)
    return box


def shortcut_list(rows: List[Tuple[str, str, str]]) -> QWidget:
    """引导页的快捷键表：键帽 | 功能名 | 一句话说明。rows=(组合键, 功能, 说明)，组合键为空的行跳过。"""
    box = QWidget()
    grid = QGridLayout(box)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(16)
    grid.setVerticalSpacing(18)
    r = 0
    for combo, name, desc in rows:
        if not combo:
            continue
        grid.addWidget(keycaps(combo), r, 0, Qt.AlignLeft | Qt.AlignVCenter)
        text = QWidget()
        col = QVBoxLayout(text)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        title = QLabel(name)
        title.setObjectName("SheetItem")
        col.addWidget(title)
        if desc:
            col.addWidget(body_label(desc, muted=True))
        grid.addWidget(text, r, 1)
        r += 1
    grid.setColumnStretch(1, 1)
    return box


def progress_body() -> Tuple[QWidget, QProgressBar, QLabel]:
    box = QWidget()
    col = QVBoxLayout(box)
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(10)
    bar = QProgressBar()
    bar.setObjectName("SheetProgress")
    bar.setRange(0, 100)
    bar.setTextVisible(False)
    bar.setFixedHeight(6)
    note = body_label("", muted=True)
    col.addWidget(bar)
    col.addWidget(note)
    return box, bar, note


def sheet_qss() -> str:
    t = theme
    return f"""
QFrame#SheetCard {{
    background: {t.MENU_BG};
    border: 1px solid {t.CARD_BORDER};
    border-radius: {CARD_RADIUS}px;
}}
QLabel#SheetTitle {{
    font-size: 17px;
    font-weight: 600;
    color: {t.TEXT_PRIMARY};
}}
QLabel#SheetBody {{
    font-size: 13px;
    color: {t.TEXT_PRIMARY};
}}
QLabel#SheetItem {{
    font-size: 13px;
    font-weight: 600;
    color: {t.TEXT_PRIMARY};
}}
QLabel#SheetMuted {{
    font-size: 12px;
    color: {t.TEXT_SECONDARY};
}}
QLabel#Keycap {{
    background: {t.FIELD_BG};
    border: 1px solid {t.FIELD_BORDER};
    border-bottom: 2px solid {t.FIELD_BORDER_HOVER};
    border-radius: 6px;
    padding: 2px 8px;
    min-width: 12px;
    font-size: 12px;
    font-weight: 600;
    color: {t.TEXT_PRIMARY};
}}
QPushButton#SheetGhost {{
    background: transparent;
    border: 1px solid transparent;
    color: {t.TEXT_SECONDARY};
}}
QPushButton#SheetGhost:hover {{
    background: {t.ACCENT_SOFT};
    color: {t.ACCENT_HOVER};
}}
QProgressBar#SheetProgress {{
    background: {t.FIELD_BG};
    border: none;
    border-radius: 3px;
}}
QProgressBar#SheetProgress::chunk {{
    background: {t.ACCENT};
    border-radius: 3px;
}}
"""
