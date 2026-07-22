"""主题横幅：标题栏下面那条 96px 的实拍照片带（含主题文案与动效）。

参考的是"换肤后整个应用一眼就换了气质"的做法：横幅是主题最浓的一处，
但只占 96px，且可一键收起——翻译窗本来就小（默认 760×620），
不能为了好看把干活的地方挤没了。
"""
from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from . import motion as motion_mod
from . import theme

HERO_HEIGHT = 96
FPS = 30


class HeroBanner(QWidget):
    collapse_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None, motion_enabled: bool = True):
        super().__init__(parent)
        self.setObjectName("HeroBanner")
        self.setFixedHeight(HERO_HEIGHT)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self._motion_enabled = motion_enabled
        self._pm: Optional[QPixmap] = None
        self._pm_token = ()
        self._engine = motion_mod.build_hero(theme.spec()["motion"], seed=7)
        self._last = time.monotonic()

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

        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / FPS))
        self._timer.timeout.connect(self._tick)
        self._sync_btn_style()

    # ---------- 主题 / 动效 ----------

    def reload(self) -> None:
        self._pm = None
        self._pm_token = ()
        self._engine = motion_mod.build_hero(theme.spec()["motion"], seed=7)
        if self._engine is not None:
            self._engine.resize(self.width(), self.height())
        self._sync_btn_style()
        self.update()

    def set_motion(self, on: bool) -> None:
        self._motion_enabled = bool(on)
        if not on:
            self._timer.stop()
        elif self.isVisible():
            self._last = time.monotonic()
            self._timer.start()
        self.update()

    def _sync_btn_style(self) -> None:
        # 横幅底图是照片，按钮沿用主题的横幅文字色才看得清
        self.collapse_btn.setStyleSheet(
            f"QPushButton {{ color: {theme.HERO_SUB_INK}; background: transparent;"
            f" border: none; padding: 2px 8px; border-radius: 8px; font-size: 12px; }}"
            f"QPushButton:hover {{ background: rgba(255,255,255,0.18);"
            f" color: {theme.HERO_INK}; }}"
        )

    def showEvent(self, event):
        super().showEvent(event)
        if self._motion_enabled and self._engine is not None:
            self._last = time.monotonic()
            self._timer.start()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._pm = None
        if self._engine is not None:
            self._engine.resize(self.width(), self.height())

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.2, now - self._last)
        self._last = now
        if self._engine is not None:
            self._engine.step(dt, self.width(), self.height())
        self.update()

    # ---------- 绘制 ----------

    def _ensure_pixmap(self) -> None:
        w, h = self.width(), self.height()
        dpr = float(self.devicePixelRatioF() or 1.0)
        token = (theme.current(), w, h, round(dpr, 2))
        if self._pm is not None and self._pm_token == token:
            return
        self._pm_token = token
        self._pm = None
        if w <= 0 or h <= 0:
            return
        path = theme.theme_asset("hero.jpg")
        if not path:
            return
        src = QPixmap(path)
        if src.isNull():
            return
        tw, th = int(w * dpr), int(h * dpr)
        scaled = src.scaled(tw, th, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        x = max(0, (scaled.width() - tw) // 2)
        y = max(0, (scaled.height() - th) // 2)
        pm = scaled.copy(x, y, tw, th)
        pm.setDevicePixelRatio(dpr)
        self._pm = pm

    def paintEvent(self, event):
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        spec = theme.spec()

        self._ensure_pixmap()
        if self._pm is not None:
            p.drawPixmap(0, 0, self._pm)
        else:
            p.fillRect(self.rect(), QColor(theme.CARD_BG))

        # 左侧压深，保证文案在任何照片上都读得清；右侧几乎不压，留出照片本身
        ink = QColor(theme.TEXT_PRIMARY) if not theme.IS_DARK else QColor(4, 6, 14)
        scrim = QLinearGradient(0, 0, w, 0)
        scrim.setColorAt(0.0, QColor(ink.red() // 3, ink.green() // 3, ink.blue() // 3, 205))
        scrim.setColorAt(0.55, QColor(ink.red() // 3, ink.green() // 3, ink.blue() // 3, 95))
        scrim.setColorAt(1.0, QColor(ink.red() // 3, ink.green() // 3, ink.blue() // 3, 25))
        p.fillRect(self.rect(), scrim)

        if self._engine is not None:
            p.save()
            p.setClipRect(self.rect())
            self._engine.draw(p, w, h)
            p.restore()

        # 文案
        title = QFont(theme.FONT_FAMILY.split(",")[0].strip('"'))
        title.setPixelSize(23)
        title.setBold(True)
        title.setLetterSpacing(QFont.PercentageSpacing, 103)
        p.setFont(title)
        p.setPen(QColor(theme.HERO_INK))
        p.drawText(QRectF(20, h * 0.20, w - 120, 30), Qt.AlignLeft | Qt.AlignVCenter,
                   spec["slogan"])
        sub = QFont(theme.FONT_FAMILY.split(",")[0].strip('"'))
        sub.setPixelSize(12)
        p.setFont(sub)
        p.setPen(QColor(theme.HERO_SUB_INK))
        p.drawText(QRectF(21, h * 0.55, w - 120, 22), Qt.AlignLeft | Qt.AlignVCenter,
                   spec["sub"])
        # 底部一条主题色细线，把横幅和内容区分开
        p.fillRect(QRectF(0, h - 2, w, 2), QColor(theme.ACCENT))
        p.end()
