"""主题横幅：标题栏下面那条 96px 的主题文案区。

**它自己不画照片、也不跑动效**——照片和动效都是背景层（Backdrop）的，横幅只是
压在那张照片"清晰段"上的一层文字。之前横幅自己贴了另一张照片，于是横幅和背景是
两张不同缩放、不同清晰度、不同色调的图硬接在一起，中间那条分割线怎么调都别扭；
现在全窗共用一张照片，横幅区域只是它没被虚化的那一段，接缝在物理上就不存在。

横幅可一键收起：翻译窗本来就小（默认 760×620），不能为了好看把干活的地方挤没了。
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from . import theme

HERO_HEIGHT = 96


class HeroBanner(QWidget):
    collapse_requested = Signal()

    def __init__(self, parent=None, motion_enabled: bool = True):
        super().__init__(parent)
        self.setObjectName("HeroBanner")
        self.setFixedHeight(HERO_HEIGHT)
        self.setAttribute(Qt.WA_StyledBackground, False)   # 背景透出下面那层照片

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 6, 8, 0)
        lay.addStretch(1)
        self.collapse_btn = QPushButton("收起", self)
        self.collapse_btn.setObjectName("Ghost")
        self.collapse_btn.setToolTip("收起主题横幅（设置页可再打开）")
        self.collapse_btn.setCursor(Qt.PointingHandCursor)
        self.collapse_btn.setFixedHeight(22)
        self.collapse_btn.clicked.connect(self.collapse_requested)
        lay.addWidget(self.collapse_btn, 0, Qt.AlignTop)
        self._sync_btn_style()

    # ---------- 主题 ----------

    def reload(self) -> None:
        self._sync_btn_style()
        self.update()

    def set_motion(self, on: bool) -> None:
        """动效在背景层，这里没有独立定时器；留着接口是为了调用方不用分情况。"""
        self.update()

    def _sync_btn_style(self) -> None:
        # 按钮压在照片上，得用主题的横幅文字色才看得清
        self.collapse_btn.setStyleSheet(
            f"QPushButton {{ color: {theme.HERO_SUB_INK}; background: transparent;"
            f" border: none; padding: 2px 8px; border-radius: 8px; font-size: 12px; }}"
            f"QPushButton:hover {{ background: rgba(255,255,255,0.20);"
            f" color: {theme.HERO_INK}; }}"
        )

    # ---------- 绘制：只有压深 + 文案 ----------

    def paintEvent(self, event):
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        spec = theme.spec()

        # 文案侧压深：左边压得住字，右边几乎不压，照片自己露出来。
        # 底部不再画任何分割线——背景层已经用一段渐变把清晰度和纱一起化开了。
        ink = QColor(theme.TEXT_PRIMARY) if not theme.IS_DARK else QColor(4, 6, 14)
        base = QColor(ink.red() // 3, ink.green() // 3, ink.blue() // 3)
        scrim = QLinearGradient(0, 0, w, 0)
        scrim.setColorAt(0.0, QColor(base.red(), base.green(), base.blue(), 190))
        scrim.setColorAt(0.52, QColor(base.red(), base.green(), base.blue(), 88))
        scrim.setColorAt(1.0, QColor(base.red(), base.green(), base.blue(), 18))
        p.fillRect(self.rect(), scrim)
        # 上下再各化开一点，横幅这块压深就不会自己变成一个方框
        soften = QLinearGradient(0, 0, 0, h)
        soften.setColorAt(0.0, QColor(base.red(), base.green(), base.blue(), 30))
        soften.setColorAt(0.45, QColor(base.red(), base.green(), base.blue(), 0))
        soften.setColorAt(1.0, QColor(base.red(), base.green(), base.blue(), 0))
        p.fillRect(self.rect(), soften)

        family = theme.FONT_FAMILY.split(",")[0].strip('"')
        title = QFont(family)
        title.setPixelSize(23)
        title.setBold(True)
        title.setLetterSpacing(QFont.PercentageSpacing, 103)
        p.setFont(title)
        p.setPen(QColor(theme.HERO_INK))
        p.drawText(QRectF(20, h * 0.18, w - 120, 30), Qt.AlignLeft | Qt.AlignVCenter,
                   spec["slogan"])
        sub = QFont(family)
        sub.setPixelSize(12)
        p.setFont(sub)
        p.setPen(QColor(theme.HERO_SUB_INK))
        p.drawText(QRectF(21, h * 0.53, w - 120, 22), Qt.AlignLeft | Qt.AlignVCenter,
                   spec["sub"])
        p.end()
