"""主题背景层：真实照片底图 + 动效。

它是 Shell 的一个子控件，不进布局、永远沉在最底、且**完全不吃鼠标事件**
（`WA_TransparentForMouseEvents`）——上面还压着标题栏、页签和卡片，背景层
一旦截住事件，拖窗和点按钮就全废了。

性能上守三条线：
1. 底图按当前尺寸和 DPR 预烘焙成 QPixmap，只在 resize（防抖 120ms）或换主题时重做；
2. 慢变内容（常春藤长出来的茎叶）烘焙进离屏层，每帧只重画活动元素；
3. 控件不可见（窗口隐藏到托盘 / 最小化）立刻停表——这个软件常年挂托盘，
   后台还在 30fps 空转是不可接受的。
"""
from __future__ import annotations

import os
import time
from typing import Optional

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from . import motion as motion_mod
from . import theme

FPS = 30
_DEBUG = bool(os.environ.get("IVYEA_BACKDROP_DEBUG"))


class Backdrop(QWidget):
    def __init__(self, parent: QWidget, motion_enabled: bool = True):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
        self._motion_enabled = motion_enabled
        self._radius = theme.WINDOW_RADIUS
        self._bg: Optional[QPixmap] = None
        self._bg_token = ()
        self._baked: Optional[QPixmap] = None
        self._engine = motion_mod.build(theme.spec()["motion"])
        self._last = time.monotonic()
        self._frame_ms = 0.0
        self._frames = 0
        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / FPS))
        self._timer.timeout.connect(self._tick)
        parent.installEventFilter(self)
        self._sync_geometry()
        self.lower()

    # ---------- 与宿主同步 ----------

    def eventFilter(self, obj, event):
        if obj is self.parent() and event.type() in (QEvent.Resize, QEvent.Show):
            self._sync_geometry()
        return False

    def _sync_geometry(self) -> None:
        p = self.parentWidget()
        if p is not None:
            self.setGeometry(0, 0, p.width(), p.height())

    def set_radius(self, radius: int) -> None:
        """窗口最大化时圆角要切成直角，跟着 Shell 走。"""
        if radius != self._radius:
            self._radius = radius
            self.update()

    def set_motion(self, on: bool) -> None:
        self._motion_enabled = bool(on)
        if not on:
            self._timer.stop()
        elif self.isVisible():
            self._last = time.monotonic()
            self._timer.start()
        self.update()

    def reload(self) -> None:
        """换主题：底图、烘焙层、动效引擎全部重建。"""
        self._bg = None
        self._bg_token = ()
        self._baked = None
        self._engine = motion_mod.build(theme.spec()["motion"])
        if self._engine is not None:
            self._engine.resize(self.width(), self.height())
        self.update()

    # ---------- 生命周期：不可见就停表 ----------

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
        self._bg = None
        self._baked = None
        if self._engine is not None:
            self._engine.resize(self.width(), self.height())

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.2, now - self._last)   # 卡顿/休眠后不要一次跳很远
        self._last = now
        if self._engine is not None:
            self._engine.step(dt, self.width(), self.height())
            if self._engine.baked:
                self._grow(dt)
        self.update()

    # ---------- 绘制 ----------

    def _dpr(self) -> float:
        return float(self.devicePixelRatioF() or 1.0)

    def _ensure_bg(self) -> None:
        w, h = self.width(), self.height()
        dpr = self._dpr()
        token = (theme.current(), w, h, round(dpr, 2))
        if self._bg is not None and self._bg_token == token:
            return
        self._bg_token = token
        self._bg = None
        if w <= 0 or h <= 0:
            return
        path = theme.theme_asset("bg.jpg")
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
        # 柔化：先降采样再放大回来。比高斯模糊便宜得多，而背景本来就要"退到后面去"，
        # 细节留着只会和卡片里的正文抢注意力（夜景/星云这类本身就疏，blur=0 不动）
        blur = int(getattr(theme, "BACKDROP_BLUR", 0) or 0)
        if blur > 1:
            small = pm.scaled(max(1, tw // blur), max(1, th // blur),
                              Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            pm = small.scaled(tw, th, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        pm.setDevicePixelRatio(dpr)
        self._bg = pm

    def _ensure_baked(self) -> Optional[QPixmap]:
        if self._baked is not None:
            return self._baked
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return None
        dpr = self._dpr()
        pm = QPixmap(int(w * dpr), int(h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        self._baked = pm
        return pm

    def _grow(self, dt: float) -> None:
        """把生长类动效的新增内容画进烘焙层。"""
        pm = self._ensure_baked()
        if pm is None:
            return
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        try:
            self._engine.grow(p, dt)
        finally:
            p.end()

    def paintEvent(self, event):
        t0 = time.perf_counter()
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        motion_mod.rounded_clip(p, w, h, self._radius)

        self._ensure_bg()
        if self._bg is not None:
            p.drawPixmap(0, 0, self._bg)
            r, g, b, a = theme.BACKDROP_VEIL
            p.fillRect(self.rect(), QColor(r, g, b, int(a * 255)))
            # 标题栏那条 38px 是透明的，底下要是恰好压着照片的暗部，字标和
            # 最小化/关闭按钮就糊进去了 —— 顶部再压一道同色渐变保证可读
            scrim = QLinearGradient(0, 0, 0, 52)
            scrim.setColorAt(0.0, QColor(r, g, b, 225))
            scrim.setColorAt(0.62, QColor(r, g, b, 120))
            scrim.setColorAt(1.0, QColor(r, g, b, 0))
            p.fillRect(QRectF(0, 0, w, 52), scrim)

        if self._engine is not None:
            if self._engine.baked and self._baked is not None:
                p.setOpacity(getattr(self._engine, "bake_alpha", lambda: 1.0)())
                p.drawPixmap(0, 0, self._baked)
                p.setOpacity(1.0)
            self._engine.draw(p, w, h)
        p.end()

        if _DEBUG:
            self._frame_ms += (time.perf_counter() - t0) * 1000
            self._frames += 1
            if self._frames % 60 == 0:
                print(f"[backdrop] {theme.current()} avg {self._frame_ms/self._frames:.2f}ms "
                      f"over {self._frames} frames")
