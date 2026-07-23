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
VEIL_LEAD = 6         # 纱在横幅底边才开始加厚——早于此就把横幅里的照片洗白了…
VEIL_TRAIL = 34       # …往下走 34px 完成：再长页签就压在半透的照片上看不清，
                      #    再短这段渐变自己会变成一条看得见的横线
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
        self._top_luma = 1.0  # 标题栏那条的平均明暗，决定字标用深色还是浅色
        self._band_luma = 1.0 # 横幅那一段的明暗，决定名句用深色还是浅色
        self._tabs_luma = 1.0 # 页签那一条的明暗
        self._tint: Optional[QPixmap] = None      # 纯色主题的顶部色块（含标题栏那条）
        self._tint_token = ()
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
        self._tint = None
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
        self._tint = None
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
            self._tint = None
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
            edge = min(0.98, band / max(1, layer_h))
            # 满 alpha 一直保到横幅底边：原来提前 0.22 就开始衰减，衰减点落在
            # 横幅内部，于是横幅下半截的照片本身就是糊的——"横幅一定要清晰"
            g.setColorAt(0.0, QColor(0, 0, 0, 255))
            g.setColorAt(edge, QColor(0, 0, 0, 255))
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
        # 拉长压薄：原来是 52px 内压到 215 alpha，浅色主题下就成了一条"没图"的白带。
        # 现在改成 96px 的缓坡（顶端 132），读起来像光从上面打下来，而不是贴了条白纸。
        # 字看不看得清不靠这层解决——顶栏文字会按背景明暗自动换深浅（见 top_luma）。
        # 只压标题栏那一条，且很薄：再厚就把横幅里的照片洗白了（珠峰的深蓝天
        # 被压成灰白就是这么来的）。标题栏文字的可读性靠自适应字色 + 字标投影。
        scrim = QLinearGradient(0, 0, 0, 70 * dpr)
        scrim.setColorAt(0.0, QColor(r, gc, b, 92))
        scrim.setColorAt(0.55, QColor(r, gc, b, 42))
        scrim.setColorAt(1.0, QColor(r, gc, b, 0))
        cp.fillRect(0, 0, tw, int(70 * dpr), scrim)
        cp.end()

        canvas.setDevicePixelRatio(dpr)
        self._bg = canvas
        self._top_luma = self._measure_luma(canvas, 0, int(38 * dpr))
        # 横幅那一段（文字压在这儿）单独量一次：顶栏亮不代表横幅也亮
        self._band_luma = self._measure_luma(
            canvas, int(44 * dpr), int(max(45, self._band) * dpr), right=0.62)
        # 页签紧贴横幅下沿，那一条也得单独量：纱在这儿才刚加厚到一半
        self._tabs_luma = self._measure_luma(
            canvas, int(self._band * dpr), int((self._band + 38) * dpr), right=0.5)

    @staticmethod
    def _measure_luma(canvas: QPixmap, y0: int, y1: int, right: float = 1.0) -> float:
        """某一横条的平均明暗（0=黑 1=白）。

        照片主题下标题栏和横幅都是透明的，字直接压在照片上：夜景要用浅色字、
        叶丛要用深色字，一刀切必然有一头看不清。取样而不是拍脑袋。
        `right` 限制取样宽度——文字只占左边一截，右边再亮也不该影响判断。
        """
        img = canvas.toImage()
        y1 = max(y0 + 1, min(y1, img.height()))
        w = max(1, int(img.width() * right))
        total = n = 0
        step = max(1, w // 50)
        for y in range(y0, y1, max(1, (y1 - y0) // 6)):
            for x in range(0, w, step):
                c = img.pixelColor(x, y)
                total += 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
                n += 1
        return (total / max(1, n)) / 255.0

    def top_luma(self) -> float:
        self._ensure_bg()
        return getattr(self, "_top_luma", 1.0 if not theme.IS_DARK else 0.0)

    def band_luma(self) -> float:
        """横幅那一段的明暗，决定名句用深色字还是浅色字。"""
        self._ensure_bg()
        return getattr(self, "_band_luma", self.top_luma())

    def tabs_luma(self) -> float:
        """页签那一条的明暗，决定页签文字用深色还是浅色。"""
        self._ensure_bg()
        return getattr(self, "_tabs_luma", self.band_luma())

    def _ensure_tint(self) -> Optional[QPixmap]:
        """纯色主题的顶部色块：从窗口最顶一直铺到横幅底部，只在底部化开。

        这块必须由背景层来画、而且要盖住标题栏那条——之前是横幅自己画的，
        色块的上边缘正好落在横幅顶部，于是横幅和顶栏之间硬生生多出一道渐变边。
        由背景层从 y=0 铺下来，上半段就和顶栏连成一片，唯一的过渡留在底部。
        """
        w, band = self.width(), self._band
        dpr = self._dpr()
        token = (theme.current(), w, band, round(dpr, 2))
        if self._tint is not None and self._tint_token == token:
            return self._tint
        self._tint_token = token
        self._tint = None
        if w <= 0 or band <= 0:
            return None
        tw, th = int(w * dpr), int(band * dpr)
        pm = QPixmap(tw, th)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        accent = QColor(theme.ACCENT)
        peak = 34 if not theme.IS_DARK else 44
        side = QLinearGradient(0, 0, tw * 0.9, 0)
        side.setColorAt(0.0, QColor(accent.red(), accent.green(), accent.blue(), peak))
        side.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 0))
        p.fillRect(0, 0, tw, th, side)
        p.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        fade = QLinearGradient(0, 0, 0, th)
        fade.setColorAt(0.0, QColor(0, 0, 0, 255))
        fade.setColorAt(0.62, QColor(0, 0, 0, 245))
        fade.setColorAt(1.0, QColor(0, 0, 0, 0))     # 只有底边化开
        p.fillRect(0, 0, tw, th, fade)
        p.end()
        pm.setDevicePixelRatio(dpr)
        self._tint = pm
        return pm

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
        elif not getattr(theme, "HAS_PHOTO", True):
            tint = self._ensure_tint()     # 纯色主题：底色由 Shell 的 QSS 画，这里只补顶部色块
            if tint is not None:
                p.drawPixmap(0, 0, tint)

        if self._engine is not None:
            if self._engine.baked and self._baked is not None:
                p.setOpacity(getattr(self._engine, "bake_alpha", lambda: 1.0)())
                p.drawPixmap(0, 0, self._baked)
                p.setOpacity(1.0)
            self._engine.draw(p, w, h)
            # 动效是画在底图之上的：藤蔓长着长着就爬过标题栏，把字标和窗口按钮盖住
            # （底图里那道顶部压深挡不住后画的东西）。这里再补一道，只压最上面一条。
            r, g, bl, _a = theme.BACKDROP_VEIL
            top = QLinearGradient(0, 0, 0, 42)
            top.setColorAt(0.0, QColor(r, g, bl, 88))
            top.setColorAt(0.6, QColor(r, g, bl, 50))
            top.setColorAt(1.0, QColor(r, g, bl, 0))
            p.fillRect(QRectF(0, 0, w, 42), top)
        p.end()

        if _DEBUG:
            self._frame_ms += (time.perf_counter() - t0) * 1000
            self._frames += 1
            if self._frames % 60 == 0:
                print(f"[backdrop] {theme.current()} avg {self._frame_ms/self._frames:.2f}ms "
                      f"over {self._frames} frames")
