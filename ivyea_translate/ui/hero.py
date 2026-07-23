"""主题横幅：标题栏下面那条 96px 的主题文案区。

**它自己不画照片、也不跑动效**——照片和动效都是背景层（Backdrop）的，横幅只是
压在那张照片"清晰段"上的一层文字。之前横幅自己贴了另一张照片，于是横幅和背景是
两张不同缩放、不同清晰度、不同色调的图硬接在一起，中间那条分割线怎么调都别扭；
现在全窗共用一张照片，横幅区域只是它没被虚化的那一段，接缝在物理上就不存在。

横幅可一键收起：翻译窗本来就小（默认 760×620），不能为了好看把干活的地方挤没了。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QLinearGradient, QPainter, QPixmap,
                           QRadialGradient)
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
        # 按钮压在照片上，得用主题的横幅文字色才看得清；
        # 纯色主题底下没有压深，hover 用主色淡底，白底衬白是看不见的
        hover_bg = ("rgba(255,255,255,0.20)" if getattr(theme, "HAS_PHOTO", True)
                    else theme.ACCENT_SOFT)
        self.collapse_btn.setStyleSheet(
            f"QPushButton {{ color: {theme.HERO_SUB_INK}; background: transparent;"
            f" border: none; padding: 2px 8px; border-radius: 8px; font-size: 12px; }}"
            f"QPushButton:hover {{ background: {hover_bg};"
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

        photo = bool(getattr(theme, "HAS_PHOTO", True))
        title_rect = QRectF(20, h * 0.18, w - 120, 30)
        sub_rect = QRectF(21, h * 0.53, w - 120, 22)

        if photo:
            # 文案背后一团柔和的压深，四周自己化开。
            # 之前是整块矩形填充，底边一刀切——横幅于是变成一个突兀的深色方框，
            # 和照片没关系，纯粹是这层压深自己画出来的边。
            ink = QColor(theme.TEXT_PRIMARY) if not theme.IS_DARK else QColor(4, 6, 14)
            base = QColor(ink.red() // 3, ink.green() // 3, ink.blue() // 3)
            radius = max(240.0, w * 0.52)
            p.save()
            p.translate(0.0, h * 0.46)
            p.scale(1.0, (h * 0.95) / max(1.0, radius * 2))   # 压扁成横向椭圆
            g = QRadialGradient(0.0, 0.0, radius)
            g.setColorAt(0.0, QColor(base.red(), base.green(), base.blue(), 205))
            g.setColorAt(0.55, QColor(base.red(), base.green(), base.blue(), 96))
            g.setColorAt(1.0, QColor(base.red(), base.green(), base.blue(), 0))
            p.setBrush(g)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(0.0, 0.0), radius, radius)
            p.restore()
        # 纯色主题这里什么都不画：顶部色块由背景层从 y=0 一路铺到横幅底部，
        # 上半段因此和标题栏连成一片，渐变只出现在横幅下沿（见 Backdrop._ensure_tint）

        family = theme.FONT_FAMILY.split(",")[0].strip('"')
        title = QFont(family)
        title.setPixelSize(23)
        title.setBold(True)
        title.setLetterSpacing(QFont.PercentageSpacing, 103)
        p.setFont(title)
        if photo:
            # 压深收窄之后，字自己带一点点投影才在任何照片上都立得住
            p.setPen(QColor(0, 0, 0, 90))
            p.drawText(title_rect.translated(0.6, 1.0), Qt.AlignLeft | Qt.AlignVCenter,
                       spec["slogan"])
        p.setPen(QColor(theme.HERO_INK))
        p.drawText(title_rect, Qt.AlignLeft | Qt.AlignVCenter, spec["slogan"])

        sub = QFont(family)
        sub.setPixelSize(12)
        p.setFont(sub)
        if photo:
            p.setPen(QColor(0, 0, 0, 70))
            p.drawText(sub_rect.translated(0.5, 0.8), Qt.AlignLeft | Qt.AlignVCenter,
                       spec["sub"])
        p.setPen(QColor(theme.HERO_SUB_INK))
        p.drawText(sub_rect, Qt.AlignLeft | Qt.AlignVCenter, spec["sub"])

        p.end()
