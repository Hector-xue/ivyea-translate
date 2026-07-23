"""六套主题的背景动效引擎。

约定：
- 每个引擎实现 `step(dt, w, h)` 与 `draw(painter, w, h)`，宿主控件（Backdrop/HeroBanner）
  只负责计时、裁剪与调度，引擎自己不碰 Qt 的窗口体系。
- **可见元素一律用真实照片精灵**（叶片/花瓣/国旗/霓虹/云雾/星野都来自 assets/themes/*/sprites，
  素材是公有领域或 CC0 的实拍图），程序只做位移、旋转、缩放、明暗与透明度；
  唯一的例外是常春藤的藤茎——那是一条线，和门户站 `site/js/ivy.js` 的做法保持一致。
- 慢变的内容（长好的藤蔓）烘焙进离屏 pixmap，每帧只重画活动元素，避免全量重绘。
"""
from __future__ import annotations

import math
import random
from typing import Dict, List, Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)

from . import theme

# 精灵缓存：路径 -> QPixmap（QPixmap 只能在有 QGuiApplication 后创建，故惰性加载）
_SPRITES: Dict[str, Optional[QPixmap]] = {}


def sprite(name: str, key: str = "") -> Optional[QPixmap]:
    """加载主题精灵；缺失返回 None，调用方须能降级。"""
    path = theme.theme_asset(name, key)
    if not path:
        return None
    if path not in _SPRITES:
        pm = QPixmap(path)
        _SPRITES[path] = None if pm.isNull() else pm
    return _SPRITES[path]


def clear_cache() -> None:
    _SPRITES.clear()


class Motion:
    """动效基类：宿主每帧调用 step()，然后 draw()。"""

    #: 需要"烘焙层"（慢变内容画一次就留着）的引擎置 True
    baked = False

    def __init__(self, seed: int = 0):
        self.rng = random.Random(seed or 20260722)
        self.t = 0.0
        self._w = self._h = 0

    def resize(self, w: int, h: int) -> None:
        """尺寸变了：默认重置（子类可保留状态）。"""
        self._w, self._h = w, h
        self.reset()

    def reset(self) -> None:
        pass

    def step(self, dt: float, w: int, h: int) -> None:
        self.t += dt

    def draw(self, p: QPainter, w: int, h: int) -> None:
        raise NotImplementedError


# ============================ 常春藤：藤蔓生长 + 真实叶片 ============================


class _Vine:
    __slots__ = ("x", "y", "ang", "curl", "speed", "leaf_gap", "since_leaf", "life", "width")

    def __init__(self, x, y, ang, rng):
        self.x, self.y, self.ang = x, y, ang
        self.curl = rng.uniform(-0.05, 0.05)
        self.speed = rng.uniform(0.9, 1.5)
        self.leaf_gap = rng.randint(7, 12)
        self.since_leaf = rng.randint(0, 6)
        self.life = 0
        self.width = rng.uniform(1.4, 2.1)


class IvyMotion(Motion):
    """藤蔓自下缘向上生长，沿途长出真实常春藤叶片；长满后整丛淡出重来（生命轮回）。"""

    baked = True
    CYCLE = 52.0        # 一轮生长+淡出的总时长（秒）
    FADE = 7.0

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.leaves = sorted(
            [pm for pm in (sprite(f"sprites/leaf_s{i}.png", "ivy") for i in range(4)) if pm],
            key=lambda pm: pm.width(),
        )
        self.vines: List[_Vine] = []
        self.live: List[dict] = []   # 尖端附近还在摆动的叶子
        self.cycle_t = 0.0

    def reset(self) -> None:
        self.vines = []
        self.live = []
        self.cycle_t = 0.0
        w, h = self._w, self._h
        if w <= 0 or h <= 0:
            return
        # 只从下缘和两侧往上爬：顶边垂下来的那根会横在标题栏上，像块随机的斑
        for i in range(4):
            x = w * self.rng.uniform(0.01 + 0.25 * i, 0.14 + 0.25 * i)
            self.vines.append(_Vine(x, h + 4, -math.pi / 2 + self.rng.uniform(-0.5, 0.5), self.rng))
        self.vines.append(_Vine(-4, h * self.rng.uniform(0.45, 0.8),
                                -self.rng.uniform(0.3, 0.9), self.rng))
        self.vines.append(_Vine(w + 4, h * self.rng.uniform(0.45, 0.8),
                                math.pi + self.rng.uniform(0.3, 0.9), self.rng))

    # --- 生长：把新长出的茎与叶烘焙进 baked 层 ---
    def grow(self, p: QPainter, dt: float) -> bool:
        """在烘焙层上推进生长；返回是否有内容更新。"""
        if not self.vines:
            return False
        w, h = self._w, self._h
        steps = max(1, int(dt / 0.033))
        dirty = False
        stem = QColor(theme.ACCENT)
        stem = QColor(stem.red() * 0.55 + 30, stem.green() * 0.55 + 30, stem.blue() * 0.5 + 24)
        for _ in range(steps):
            for v in list(self.vines):
                v.life += 1
                v.curl += self.rng.uniform(-0.012, 0.012)
                v.curl = max(-0.09, min(0.09, v.curl))
                v.ang += v.curl
                nx = v.x + math.cos(v.ang) * v.speed * 2.4
                ny = v.y + math.sin(v.ang) * v.speed * 2.4
                p.setPen(QPen(stem, v.width, Qt.SolidLine, Qt.RoundCap))
                p.setOpacity(0.55)
                p.drawLine(QPointF(v.x, v.y), QPointF(nx, ny))
                dirty = True
                v.x, v.y = nx, ny
                v.since_leaf += 1
                if v.since_leaf >= v.leaf_gap and self.leaves:
                    v.since_leaf = 0
                    self._bake_leaf(p, v)
                if v.life > 260 or not (-40 < v.x < w + 40 and -40 < v.y < h + 40):
                    self.vines.remove(v)
                elif v.life % 90 == 0 and len(self.vines) < 7:
                    branch = _Vine(v.x, v.y, v.ang + self.rng.choice((-0.9, 0.9)), self.rng)
                    branch.life = 120
                    self.vines.append(branch)
        p.setOpacity(1.0)
        return dirty

    def _bake_leaf(self, p: QPainter, v: _Vine) -> None:
        pm = self.rng.choice(self.leaves)
        size = self.rng.uniform(16, 30)
        ang = v.ang + math.pi / 2 + self.rng.uniform(-0.7, 0.7)
        p.save()
        p.setOpacity(self.rng.uniform(0.55, 0.85))
        p.translate(v.x, v.y)
        p.rotate(math.degrees(ang))
        scale = size / max(1, pm.height())
        wpx, hpx = pm.width() * scale, pm.height() * scale
        p.drawPixmap(QRectF(-wpx / 2, -hpx * 0.15, wpx, hpx), pm, QRectF(pm.rect()))
        p.restore()
        if len(self.live) < 14:
            self.live.append({"x": v.x, "y": v.y, "ang": ang, "size": size,
                              "pm": pm, "phase": self.rng.uniform(0, 6.3)})

    def step(self, dt: float, w: int, h: int) -> None:
        super().step(dt, w, h)
        self.cycle_t += dt
        if self.cycle_t > self.CYCLE:
            self.cycle_t = 0.0
            self.reset()

    def bake_alpha(self) -> float:
        """整丛的淡出系数：一轮末尾整体褪去，再从头长。"""
        left = self.CYCLE - self.cycle_t
        return max(0.0, min(1.0, left / self.FADE))

    def draw(self, p: QPainter, w: int, h: int) -> None:
        """活动层：尖端附近的叶子随风轻摆。"""
        alpha = self.bake_alpha()
        for lf in self.live:
            sway = math.sin(self.t * 1.1 + lf["phase"]) * 0.09
            pm = lf["pm"]
            p.save()
            p.setOpacity(0.75 * alpha)
            p.translate(lf["x"], lf["y"])
            p.rotate(math.degrees(lf["ang"] + sway))
            scale = lf["size"] / max(1, pm.height())
            wpx, hpx = pm.width() * scale, pm.height() * scale
            p.drawPixmap(QRectF(-wpx / 2, -hpx * 0.15, wpx, hpx), pm, QRectF(pm.rect()))
            p.restore()
        p.setOpacity(1.0)


# ============================ 爱国风：红旗丝绸飘动 + 金色微粒 ============================


class FlagMotion(Motion):
    """真实旗面按列做正弦位移 + 明暗着色 = 丝绸起伏；再飘一层金色微粒。"""

    SLICE = 5          # 离屏层里的切片宽（层本身是降分辨率的，别按屏幕像素算）
    LAYER_W = 380      # 离屏层最大宽度：旗面是 32% 不透明度的柔化装饰，
                       # 按屏幕分辨率画纯属浪费（实测每帧 30ms -> 3ms）

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.flag = sprite("sprites/flag.png", "patriot")
        self.motes: List[dict] = []
        self._layer_pm: Optional[QPixmap] = None
        self._mote_pm: Optional[QPixmap] = None
        self._layer_t = -99.0

    def reset(self) -> None:
        w, h = self._w, self._h
        self.motes = [{
            "x": self.rng.uniform(0, max(1, w)),
            "y": self.rng.uniform(0, max(1, h)),
            "r": self.rng.uniform(1.0, 2.6),
            "v": self.rng.uniform(6, 20),
            "phase": self.rng.uniform(0, 6.3),
        } for _ in range(26)]

    def step(self, dt: float, w: int, h: int) -> None:
        super().step(dt, w, h)
        for m in self.motes:
            m["y"] -= m["v"] * dt
            m["x"] += math.sin(self.t * 0.7 + m["phase"]) * 6 * dt
            if m["y"] < -6:
                m["y"] = h + self.rng.uniform(0, 30)
                m["x"] = self.rng.uniform(0, max(1, w))

    def _mote(self) -> QPixmap:
        """一颗金色微粒烘焙一次重复用：径向渐变每帧算 26 次是笔冤枉开销。"""
        if self._mote_pm is None:
            size = 32
            pm = QPixmap(size, size)
            pm.fill(Qt.transparent)
            mp = QPainter(pm)
            mp.setRenderHint(QPainter.Antialiasing, True)
            g = QRadialGradient(size / 2, size / 2, size / 2)
            g.setColorAt(0.0, QColor(255, 214, 120, 200))
            g.setColorAt(1.0, QColor(255, 196, 90, 0))
            mp.setBrush(g)
            mp.setPen(Qt.NoPen)
            mp.drawEllipse(0, 0, size, size)
            mp.end()
            self._mote_pm = pm
        return self._mote_pm

    def _layer(self, w: int, h: int) -> QPixmap:
        if (self._layer_pm is None or self._layer_pm.width() != w
                or self._layer_pm.height() != h):
            self._layer_pm = QPixmap(max(1, w), max(1, h))
        return self._layer_pm

    def draw_flag(self, p: QPainter, rect: QRectF, opacity: float = 1.0,
                  feather: bool = True) -> None:
        """旗面按列做正弦位移 + 明暗着色。

        先画进离屏层再羽化边缘整体贴上：直接往背景上画会得到一块边缘齐刷刷的
        矩形，看着像贴了张图而不是"飘在那儿"。
        """
        if self.flag is None or rect.width() < 8 or rect.height() < 8:
            return
        scale = min(1.0, self.LAYER_W / max(1.0, rect.width()))
        lw = max(8, int(rect.width() * scale))
        lh = max(8, int(rect.height() * scale))
        stale = (self._layer_pm is None or self._layer_pm.width() != lw
                 or self._layer_pm.height() != lh
                 or self.t - self._layer_t >= 1 / 15.0)
        layer = self._layer(lw, lh)
        if not stale:
            # 丝绸起伏很慢，15fps 重画一次肉眼分辨不出，省掉一半绘制开销
            p.save()
            p.setOpacity(opacity)
            p.drawPixmap(rect, layer, QRectF(layer.rect()))
            p.restore()
            p.setOpacity(1.0)
            return
        self._layer_t = self.t
        layer.fill(Qt.transparent)
        lp = QPainter(layer)
        lp.setRenderHint(QPainter.SmoothPixmapTransform, False)  # 相邻切片不留缝
        src_w, src_h = self.flag.width(), self.flag.height()
        n = max(1, lw // self.SLICE)
        sw = src_w / n
        col_w = lw / n
        for i in range(n):
            phase = self.t * 1.7 - i * 0.16
            dy = math.sin(phase) * lh * 0.07
            squeeze = 1.0 + math.cos(phase) * 0.05
            x = i * col_w
            dst = QRectF(x, dy, col_w + 1.2, lh * squeeze)
            lp.drawPixmap(dst, self.flag, QRectF(i * sw, 0, sw, src_h))
            # 丝绸的明暗：波谷压暗、波峰提亮
            shade = math.sin(phase + 0.7)
            if abs(shade) < 0.28:
                continue          # 波形平缓处压根看不出明暗，别浪费一次填充
            if shade < 0:
                lp.setOpacity(min(0.30, -shade * 0.30))
                lp.fillRect(dst, QColor(70, 0, 8))
            else:
                lp.setOpacity(min(0.18, shade * 0.18))
                lp.fillRect(dst, QColor(255, 228, 172))
            lp.setOpacity(1.0)
        if feather:
            # DestinationIn 用渐变去乘 alpha：左右一遍、上下一遍，四边都化开
            lp.setCompositionMode(QPainter.CompositionMode_DestinationIn)
            gx = QLinearGradient(0, 0, lw, 0)
            gx.setColorAt(0.0, QColor(0, 0, 0, 0))
            gx.setColorAt(0.38, QColor(0, 0, 0, 255))
            gx.setColorAt(1.0, QColor(0, 0, 0, 255))
            lp.fillRect(0, 0, lw, lh, gx)
            gy = QLinearGradient(0, 0, 0, lh)
            gy.setColorAt(0.0, QColor(0, 0, 0, 0))
            gy.setColorAt(0.30, QColor(0, 0, 0, 255))
            gy.setColorAt(0.72, QColor(0, 0, 0, 255))
            gy.setColorAt(1.0, QColor(0, 0, 0, 0))
            lp.fillRect(0, 0, lw, lh, gy)
        lp.end()
        p.save()
        p.setOpacity(opacity)
        p.drawPixmap(rect, layer, QRectF(layer.rect()))
        p.restore()
        p.setOpacity(1.0)

    def draw(self, p: QPainter, w: int, h: int) -> None:
        # 旗面垂在右下：顶部那段是照片最好看的地方（横幅文案也在那儿），
        # 旗子压上去只会糊成一片暗红
        fh = h * 0.62
        fw = fh * 1.5
        self.draw_flag(p, QRectF(w - fw * 0.74, h * 0.34, fw, fh), opacity=0.26)
        mote = self._mote()
        for m in self.motes:
            d = m["r"] * 6.4
            p.drawPixmap(QRectF(m["x"] - d / 2, m["y"] - d / 2, d, d), mote,
                         QRectF(mote.rect()))


# ============================ 星海：视差星野 + 流星 ============================


class StarsMotion(Motion):
    """三层真实星野瓦片以不同速度漂移（视差），偶尔一道流星划过。"""

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.tile = sprite("sprites/stars.png", "starfield")
        self.layers = [
            {"scale": 1.35, "speed": 2.4, "op": 0.30, "off": 0.0},
            {"scale": 1.0, "speed": 5.0, "op": 0.45, "off": 137.0},
            {"scale": 0.72, "speed": 9.0, "op": 0.60, "off": 61.0},
        ]
        self.meteor: Optional[dict] = None
        self.next_meteor = 4.0
        self._scaled: List[Optional[QPixmap]] = [None] * len(self.layers)

    def step(self, dt: float, w: int, h: int) -> None:
        super().step(dt, w, h)
        if self.meteor:
            self.meteor["p"] += dt / self.meteor["dur"]
            if self.meteor["p"] >= 1.0:
                self.meteor = None
                self.next_meteor = self.rng.uniform(6.0, 16.0)
        else:
            self.next_meteor -= dt
            if self.next_meteor <= 0:
                self.meteor = {
                    "p": 0.0,
                    "dur": self.rng.uniform(0.7, 1.2),
                    "x": self.rng.uniform(-0.1, 0.8),
                    "y": self.rng.uniform(-0.05, 0.5),
                    "len": self.rng.uniform(0.16, 0.30),
                }

    def draw(self, p: QPainter, w: int, h: int) -> None:
        if self.tile is not None:
            for i, ly in enumerate(self.layers):
                tile = self._scaled[i]
                if tile is None:
                    tw0 = max(8, int(self.tile.width() * ly["scale"]))
                    th0 = max(8, int(self.tile.height() * ly["scale"]))
                    tile = self.tile.scaled(tw0, th0, Qt.IgnoreAspectRatio,
                                            Qt.SmoothTransformation)
                    self._scaled[i] = tile
                tw, th = tile.width(), tile.height()
                dx = -((self.t * ly["speed"] + ly["off"]) % tw)
                dy = -((self.t * ly["speed"] * 0.35 + ly["off"]) % th)
                # 呼吸：整层亮度缓慢起伏，像大气抖动
                breath = 0.86 + 0.14 * math.sin(self.t * 0.5 + i)
                p.setOpacity(ly["op"] * breath)
                y = dy
                while y < h:
                    x = dx
                    while x < w:
                        p.drawPixmap(QPointF(x, y), tile)   # 整像素平铺，不再每帧缩放
                        x += tw
                    y += th
            p.setOpacity(1.0)
        m = self.meteor
        if m:
            x0, y0 = m["x"] * w, m["y"] * h
            travel = m["len"] * (w + h) * 0.8
            cx = x0 + travel * m["p"] * 1.4
            cy = y0 + travel * m["p"] * 0.7
            tail = travel * 0.42
            fade = math.sin(math.pi * m["p"])
            grad = QLinearGradient(cx, cy, cx - tail, cy - tail * 0.5)
            grad.setColorAt(0.0, QColor(255, 255, 255, int(220 * fade)))
            grad.setColorAt(0.35, QColor(160, 200, 255, int(90 * fade)))
            grad.setColorAt(1.0, QColor(120, 160, 255, 0))
            p.setPen(QPen(grad, 2.0, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(cx, cy), QPointF(cx - tail, cy - tail * 0.5))


# ============================ 樱花：真实花瓣飘落 ============================


class PetalsMotion(Motion):
    """真实花瓣精灵飘落：正弦横摆 + 自转 + 远近两层视差。"""

    COUNT = 18

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.sprites = [pm for pm in (sprite(f"sprites/petal_{i}.png", "sakura")
                                      for i in range(6)) if pm]
        self.items: List[dict] = []

    def reset(self) -> None:
        w, h = self._w, self._h
        if not self.sprites or w <= 0:
            self.items = []
            return
        self.items = [self._spawn(w, h, first=True) for _ in range(self.COUNT)]

    def _spawn(self, w, h, first=False) -> dict:
        depth = self.rng.uniform(0.45, 1.0)   # 近大快、远小慢
        return {
            "pm": self.rng.choice(self.sprites),
            "x": self.rng.uniform(-20, w + 20),
            "y": self.rng.uniform(-30, h) if first else self.rng.uniform(-90, -10),
            "size": 15 + 26 * depth,
            "vy": 16 + 40 * depth,
            "sway": self.rng.uniform(10, 28) * depth,
            "phase": self.rng.uniform(0, 6.3),
            "spin": self.rng.uniform(-1.5, 1.5),
            "ang": self.rng.uniform(0, 360),
            "op": 0.55 + 0.40 * depth,
        }

    def step(self, dt: float, w: int, h: int) -> None:
        super().step(dt, w, h)
        for it in self.items:
            it["y"] += it["vy"] * dt
            it["x"] += math.sin(self.t * 1.3 + it["phase"]) * it["sway"] * dt
            it["ang"] += it["spin"] * 60 * dt
            if it["y"] > h + 40:
                it.update(self._spawn(w, h))

    def draw(self, p: QPainter, w: int, h: int) -> None:
        for it in self.items:
            pm = it["pm"]
            scale = it["size"] / max(1, max(pm.width(), pm.height()))
            wpx, hpx = pm.width() * scale, pm.height() * scale
            p.save()
            p.setOpacity(it["op"])
            p.translate(it["x"], it["y"])
            p.rotate(it["ang"])
            p.drawPixmap(QRectF(-wpx / 2, -hpx / 2, wpx, hpx), pm, QRectF(pm.rect()))
            p.restore()
        p.setOpacity(1.0)


# ============================ 赛博：霓虹呼吸 + 扫描光带 ============================


class NeonMotion(Motion):
    """真实霓虹招牌抠像做呼吸辉光，配一条缓慢下扫的扫描带。"""

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.neon = sprite("sprites/neon.png", "cyber")

    def draw(self, p: QPainter, w: int, h: int) -> None:
        if self.neon is not None:
            pulse = 0.16 + 0.12 * (0.5 + 0.5 * math.sin(self.t * 1.6))
            nw = w * 0.62
            nh = nw * self.neon.height() / max(1, self.neon.width())
            drift = math.sin(self.t * 0.25) * w * 0.012
            p.setOpacity(pulse)
            p.drawPixmap(QRectF(w - nw * 0.96 + drift, h - nh * 1.05, nw, nh),
                         self.neon, QRectF(self.neon.rect()))
            p.setOpacity(1.0)
        # 扫描带：一条自上而下循环的青色光带
        band_h = max(40.0, h * 0.16)
        y = ((self.t * 46.0) % (h + band_h)) - band_h
        grad = QLinearGradient(0, y, 0, y + band_h)
        accent = QColor(theme.ACCENT)
        grad.setColorAt(0.0, QColor(accent.red(), accent.green(), accent.blue(), 0))
        grad.setColorAt(0.5, QColor(accent.red(), accent.green(), accent.blue(), 26))
        grad.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 0))
        p.fillRect(QRectF(0, y, w, band_h), grad)


# ============================ 雪山：云雾横移 ============================


class FogMotion(Motion):
    """真实云雾照片抠出的云带，分三层不同速度横向缓移。"""

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.fog = sprite("sprites/fog.png", "alpine")
        self.layers = [
            {"y": 0.34, "scale": 1.5, "speed": 5.0, "op": 0.22},
            {"y": 0.56, "scale": 1.1, "speed": 8.5, "op": 0.28},
            {"y": 0.78, "scale": 0.8, "speed": 13.0, "op": 0.20},
        ]

    def draw(self, p: QPainter, w: int, h: int) -> None:
        if self.fog is None:
            return
        for i, ly in enumerate(self.layers):
            fw = max(60.0, w * ly["scale"])
            fh = fw * self.fog.height() / max(1, self.fog.width())
            x = -((self.t * ly["speed"] + i * 311) % (fw))
            y = h * ly["y"] - fh / 2 + math.sin(self.t * 0.3 + i) * h * 0.01
            p.setOpacity(ly["op"] * (0.85 + 0.15 * math.sin(self.t * 0.4 + i * 1.7)))
            while x < w:
                p.drawPixmap(QRectF(x, y, fw, fh), self.fog, QRectF(self.fog.rect()))
                x += fw - 1
        p.setOpacity(1.0)


class LeafDriftMotion(Motion):
    """常春藤的轻量版：几片真实叶子在带状区域里慢慢飘、轻轻摆。

    横幅只有 96px 高，跑完整的藤蔓生长既看不清也浪费——那套留给整窗背景。
    """

    COUNT = 7

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self.sprites = [pm for pm in (sprite(f"sprites/leaf_s{i}.png", "ivy")
                                      for i in range(4)) if pm]
        self.items: List[dict] = []

    def reset(self) -> None:
        w, h = self._w, self._h
        if not self.sprites or w <= 0:
            self.items = []
            return
        self.items = [{
            "pm": self.rng.choice(self.sprites),
            "x": self.rng.uniform(0, w),
            "y": self.rng.uniform(h * 0.1, h * 0.9),
            "size": self.rng.uniform(h * 0.22, h * 0.5),
            "vx": self.rng.uniform(-7, -2),
            "phase": self.rng.uniform(0, 6.3),
            "ang": self.rng.uniform(-40, 40),
            "op": self.rng.uniform(0.30, 0.65),
        } for _ in range(self.COUNT)]

    def step(self, dt: float, w: int, h: int) -> None:
        super().step(dt, w, h)
        for it in self.items:
            it["x"] += it["vx"] * dt
            if it["x"] < -60:
                it["x"] = w + self.rng.uniform(10, 80)
                it["y"] = self.rng.uniform(h * 0.1, h * 0.9)

    def draw(self, p: QPainter, w: int, h: int) -> None:
        for it in self.items:
            pm = it["pm"]
            sway = math.sin(self.t * 0.9 + it["phase"]) * 7
            scale = it["size"] / max(1, pm.height())
            wpx, hpx = pm.width() * scale, pm.height() * scale
            p.save()
            p.setOpacity(it["op"])
            p.translate(it["x"], it["y"] + math.sin(self.t * 0.6 + it["phase"]) * h * 0.04)
            p.rotate(it["ang"] + sway)
            p.drawPixmap(QRectF(-wpx / 2, -hpx / 2, wpx, hpx), pm, QRectF(pm.rect()))
            p.restore()
        p.setOpacity(1.0)


_ENGINES = {
    "ivy": IvyMotion,
    "flag": FlagMotion,
    "stars": StarsMotion,
    "petals": PetalsMotion,
    "neon": NeonMotion,
    "fog": FogMotion,
}


def build(motion_key: str, seed: int = 0) -> Optional[Motion]:
    cls = _ENGINES.get(motion_key)
    return cls(seed) if cls else None


def build_hero(motion_key: str, seed: int = 0) -> Optional[Motion]:
    """横幅用的动效：常春藤换成轻量飘叶，其余与背景层同款。"""
    if motion_key == "ivy":
        return LeafDriftMotion(seed)
    return build(motion_key, seed)


def rounded_clip(p: QPainter, w: int, h: int, radius: int) -> None:
    """把绘制裁进圆角矩形（窗体圆角；radius=0 时不裁）。"""
    if radius <= 0:
        return
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, w, h), radius, radius)
    p.setClipPath(path)
