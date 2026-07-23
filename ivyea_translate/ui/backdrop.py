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
FADE_CLARITY = 64     # 清晰→虚化的过渡带：可以慢慢化，不影响可读性
VEIL_LEAD = 62        # 纱从横幅下半段就开始加厚（拉得够长才不会自己成为一条横线）…
VEIL_TRAIL = 14       # …并在页签文字之前走满，否则页签压在半透的照片上看不清
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
        self._band = 0        # 顶部清晰段高度，由 MainWindow 按标题栏+横幅算好传进来
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
        self._sync_timer()
        self.update()

    def _sync_timer(self) -> None:
        """定时器只在"开了动效 + 这套主题真有动效 + 控件可见"时才跑。

        纯色主题没有动效引擎，早先这里照样把表开起来，等于每秒 30 次重画同一张图。
        """
        want = self._motion_enabled and self._engine is not None and self.isVisible()
        if want and not self._timer.isActive():
            self._last = time.monotonic()
            self._timer.start()
        elif not want and self._timer.isActive():
            self._timer.stop()

    def reload(self) -> None:
        """换主题：底图、烘焙层、动效引擎全部重建。"""
        self._bg = None
        self._bg_token = ()
        self._baked = None
        self._engine = motion_mod.build(theme.spec()["motion"])
        if self._engine is not None:
            self._engine.resize(self.width(), self.height())
        self._sync_timer()      # 换到/换离纯色主题时，表要跟着停或起
        self.update()

    # ---------- 生命周期：不可见就停表 ----------

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_timer()

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

    def set_band(self, height: int) -> None:
        """顶部"清晰段"的高度（标题栏 + 主题横幅）。横幅收起时只剩标题栏那条。"""
        height = max(0, int(height))
        if height != self._band:
            self._band = height
            self._bg = None
            self.update()

    def _ensure_bg(self) -> None:
        """把整张背景**一次性烘焙好**：同一张照片，顶部清晰、往下渐虚，纱也跟着渐厚。

        之前横幅是另一张照片硬贴在背景上，两张图的缩放、清晰度、色调都不一样，
        中间还压了一条实线——三重割裂叠在一起，接缝怎么调都别扭。现在全窗就一张
        照片：顶部那段留清晰（横幅文案压在上面），往下用一段渐变同时化开清晰度和
        纱的厚度，物理上不存在"两张图相接"这回事，也就没有缝可看。
        """
        w, h = self.width(), self.height()
        dpr = self._dpr()
        token = (theme.current(), w, h, round(dpr, 2), self._band)
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
        focus = float(getattr(theme, "BACKDROP_FOCUS", 0.5))
        scaled = src.scaled(tw, th, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        x = max(0, (scaled.width() - tw) // 2)
        y = int(max(0, scaled.height() - th) * focus)   # 让照片最好看的一段落在顶部
        sharp = scaled.copy(x, y, tw, th)

        # 柔化：先降采样再放大回来。比高斯模糊便宜得多，而背景本来就要"退到后面去"，
        # 细节留着只会和卡片里的正文抢注意力（夜景/星云这类本身就疏，blur=0 不动）
        blur = int(getattr(theme, "BACKDROP_BLUR", 0) or 0)
        if blur > 1:
            small = sharp.scaled(max(1, tw // blur), max(1, th // blur),
                                 Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            soft = small.scaled(tw, th, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        else:
            soft = sharp

        canvas = QPixmap(tw, th)
        canvas.fill(Qt.transparent)
        cp = QPainter(canvas)
        cp.setRenderHint(QPainter.SmoothPixmapTransform, True)
        cp.drawPixmap(0, 0, soft)

        band = int(self._band * dpr)
        fade = int(FADE_CLARITY * dpr)
        if band > 0 and soft is not sharp:
            # 清晰段：画进离屏层再用竖直渐变乘 alpha，让它自己化进虚化区
            layer_h = min(th, band + fade)
            layer = QPixmap(tw, layer_h)
            layer.fill(Qt.transparent)
            lp = QPainter(layer)
            lp.drawPixmap(0, 0, sharp, 0, 0, tw, layer_h)
            lp.setCompositionMode(QPainter.CompositionMode_DestinationIn)
            g = QLinearGradient(0, 0, 0, layer_h)
            edge = band / max(1, layer_h)
            g.setColorAt(0.0, QColor(0, 0, 0, 255))
            g.setColorAt(max(0.0, edge - 0.22), QColor(0, 0, 0, 255))
            g.setColorAt(1.0, QColor(0, 0, 0, 0))
            lp.fillRect(0, 0, tw, layer_h, g)
            lp.end()
            cp.drawPixmap(0, 0, layer)

        # 纱：横幅段薄（照片要浓），过渡带里渐厚，内容区厚到卡片一放上去就浮起来
        r, gc, b, a = theme.BACKDROP_VEIL
        top_a = float(getattr(theme, "BACKDROP_VEIL_TOP", a))
        veil = QLinearGradient(0, 0, 0, th)
        lead = max(0.0, (band - VEIL_LEAD * dpr)) / max(1, th)
        trail = min(1.0, (band + VEIL_TRAIL * dpr) / max(1, th))
        veil.setColorAt(0.0, QColor(r, gc, b, int(top_a * 255)))
        veil.setColorAt(min(0.999, lead), QColor(r, gc, b, int(top_a * 255)))
        veil.setColorAt(max(min(1.0, trail), lead + 0.001), QColor(r, gc, b, int(a * 255)))
        veil.setColorAt(1.0, QColor(r, gc, b, int(a * 255)))
        cp.fillRect(0, 0, tw, th, veil)

        # 标题栏那条 38px 是透明的，底下要是恰好压着照片的暗部，字标和
        # 最小化/关闭按钮就糊进去了 —— 顶部再压一道同色渐变保证可读
        scrim = QLinearGradient(0, 0, 0, 52 * dpr)
        scrim.setColorAt(0.0, QColor(r, gc, b, 215))
        scrim.setColorAt(0.62, QColor(r, gc, b, 110))
        scrim.setColorAt(1.0, QColor(r, gc, b, 0))
        cp.fillRect(0, 0, tw, int(52 * dpr), scrim)
        cp.end()

        canvas.setDevicePixelRatio(dpr)
        self._bg = canvas

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
            p.drawPixmap(0, 0, self._bg)   # 纱与顶部压深都已烘焙在里面，这里只是一次贴图

        if self._engine is not None:
            if self._engine.baked and self._baked is not None:
                p.setOpacity(getattr(self._engine, "bake_alpha", lambda: 1.0)())
                p.drawPixmap(0, 0, self._baked)
                p.setOpacity(1.0)
            self._engine.draw(p, w, h)
            # 动效是画在底图之上的：藤蔓长着长着就爬过标题栏，把字标和窗口按钮盖住
            # （底图里那道顶部压深挡不住后画的东西）。这里再补一道，只压最上面一条。
            r, g, bl, _a = theme.BACKDROP_VEIL
            top = QLinearGradient(0, 0, 0, 46)
            top.setColorAt(0.0, QColor(r, g, bl, 190))
            top.setColorAt(0.55, QColor(r, g, bl, 120))
            top.setColorAt(1.0, QColor(r, g, bl, 0))
            p.fillRect(QRectF(0, 0, w, 46), top)
        p.end()

        if _DEBUG:
            self._frame_ms += (time.perf_counter() - t0) * 1000
            self._frames += 1
            if self._frames % 60 == 0:
                print(f"[backdrop] {theme.current()} avg {self._frame_ms/self._frames:.2f}ms "
                      f"over {self._frames} frames")
