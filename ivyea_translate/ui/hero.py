"""主题横幅：标题栏下面那条 96px 的名句区。

**它自己不画照片、也不画底色**——照片、动效、纯色主题的顶部色块都归背景层
（Backdrop）管，横幅只负责压在那上面的一层字。这么分工是因为横幅一旦自己画底，
它的上边缘就会和标题栏之间多出一道线，怎么调都别扭。

显示内容是随机名句（见 `ivyea_translate/quotes.py`）：每 25 秒换一句，点一下也能换。
排版按字数自适应——五言七言用大字排一行，长的小令或台词自动降字号并按标点折行，
出处永远右对齐吊在末行下方，像书里引诗的样子，而不是把一整段平铺开。

横幅可一键收起：翻译窗本来就小（默认 760×620），不能为了好看把干活的地方挤没了。
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QPainter, QPainterPath,
                           QPen, QPixmap, QRadialGradient)
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from .. import quotes as quotes_mod
from . import theme

HERO_HEIGHT = 96
ROTATE_MS = 25_000       # 25 秒换一句：够读完，又不至于盯着同一句发呆
FADE_MS = 420
PAD_L = 24
PAD_R = 96               # 右上角还有个"收起"按钮，别让字顶上去
#: 正文字号阶梯 (字号, 允许行数)：从大往小试，取第一个放得下的
SIZE_LADDER = ((25, 1), (22, 1), (20, 2), (18, 2), (16, 2), (15, 3), (14, 3))
#: 诗词用楷体最像样；系统没有就一路回退到界面字体
QUOTE_FAMILIES = ["Kaiti SC", "KaiTi", "STKaiti", "楷体", "TW-Kai",
                  "Noto Serif CJK SC", "Source Han Serif SC", "Songti SC", "SimSun"]


class HeroBanner(QWidget):
    collapse_requested = Signal()

    def __init__(self, parent=None, motion_enabled: bool = True):
        super().__init__(parent)
        self.setObjectName("HeroBanner")
        self.setFixedHeight(HERO_HEIGHT)
        self.setAttribute(Qt.WA_StyledBackground, False)   # 背景透出下面那层
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("点一下换一句")

        self._motion_enabled = motion_enabled
        self._light_ink = True      # 照片主题下由 MainWindow 按实测明暗设定
        self._deck = quotes_mod.Deck()
        self._quote: Tuple[str, str] = self._deck.draw()
        self._next: Optional[Tuple[str, str]] = None
        self._fade = 1.0
        self._fade_start = 0.0
        self._layer = None            # 烘焙好的文字层，每帧只贴一次图
        self._layer_token_v = ()

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

        self._rotate = QTimer(self)
        self._rotate.setInterval(ROTATE_MS)
        self._rotate.timeout.connect(self.next_quote)
        self._fade_timer = QTimer(self)
        self._fade_timer.setInterval(33)
        self._fade_timer.timeout.connect(self._on_fade_tick)

    # ---------- 名句轮播 ----------

    def next_quote(self) -> None:
        """换下一句：开了动效就交叉淡入淡出，关了就直接换。"""
        nxt = self._deck.draw()
        if not self._motion_enabled:
            self._quote = nxt
            self._layer = None
            self.update()
            return
        self._next = nxt
        self._fade_start = time.monotonic()
        if not self._fade_timer.isActive():
            self._fade_timer.start()

    def _on_fade_tick(self) -> None:
        t = (time.monotonic() - self._fade_start) * 1000 / FADE_MS
        if t >= 1.0:
            self._fade_timer.stop()
            if self._next is not None:
                self._quote, self._next = self._next, None
                self._layer = None
            self._fade = 1.0
        elif t < 0.5:
            self._fade = 1.0 - t * 2          # 前半程：旧句淡出
        else:
            if self._next is not None:        # 过半换成新句，再淡入
                self._quote, self._next = self._next, None
                self._layer = None
            self._fade = (t - 0.5) * 2
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.next_quote()
        else:
            super().mousePressEvent(event)

    # ---------- 主题 ----------

    def reload(self) -> None:
        self._sync_btn_style()
        self._layer = None
        self.update()

    def set_ink(self, light: bool) -> None:
        """名句用浅色字还是深色字。

        照片主题下横幅是透明的，字直接压在照片上。原来一律白字 + 一团深压深，
        遇上叶丛、樱花这种亮照片，那团压深就成了一块灰云。现在按背景实测明暗来定：
        暗照片配白字 + 淡淡压深，亮照片配深色字 + 一点提亮，都不需要糊一大块。
        """
        light = bool(light)
        if light != self._light_ink:
            self._light_ink = light
            self._layer = None
            self.update()

    def set_motion(self, on: bool) -> None:
        """动效在背景层；这里只决定换句时淡入淡出还是硬切。"""
        self._motion_enabled = bool(on)
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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layer = None

    def showEvent(self, event):
        super().showEvent(event)
        self._rotate.start()

    def hideEvent(self, event):
        self._rotate.stop()
        self._fade_timer.stop()
        super().hideEvent(event)

    # ---------- 排版 ----------

    @staticmethod
    def _quote_font(px: int) -> QFont:
        f = QFont()
        f.setFamilies(QUOTE_FAMILIES + [theme.FONT_FAMILY.split(",")[0].strip('"')])
        f.setPixelSize(px)
        f.setLetterSpacing(QFont.PercentageSpacing, 103)
        return f

    @staticmethod
    def _wrap(text: str, fm: QFontMetricsF, width: float,
              max_lines: int) -> Optional[List[str]]:
        """按标点断行；放不下返回 None（交给下一档更小的字号）。

        中文没有空格，只能靠标点断——在「，。；？！、」后面允许换行；
        实在断不开才在字符间硬断。优先让每行收在标点上，读起来才像诗。
        """
        breakable = "，。；：？！、）》」』"
        lines: List[str] = []
        cur = ""
        for i, ch in enumerate(text):
            cur += ch
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if fm.horizontalAdvance(cur + nxt) > width:
                cut = max((cur.rfind(c) for c in breakable), default=-1)
                if 0 <= cut < len(cur) - 1:
                    lines.append(cur[:cut + 1])
                    cur = cur[cut + 1:]
                else:
                    lines.append(cur)
                    cur = ""
            elif ch in breakable and nxt and fm.horizontalAdvance(cur) > width * 0.86:
                lines.append(cur)
                cur = ""
            if len(lines) > max_lines:
                return None
        if cur:
            lines.append(cur)
        lines = [ln for ln in lines if ln]
        return None if len(lines) > max_lines else lines

    def _layout_quote(self, width: float):
        """挑一个放得下的字号，返回 (字号, 行列表)。"""
        text = self._quote[0]
        for px, max_lines in SIZE_LADDER:
            fm = QFontMetricsF(self._quote_font(px))
            if fm.horizontalAdvance(text) <= width:     # 一行放得下就用大字号
                return px, [text]
            lines = self._wrap(text, fm, width, max_lines)
            if lines:
                return px, lines
        px = SIZE_LADDER[-1][0]
        fm = QFontMetricsF(self._quote_font(px))
        return px, (self._wrap(text, fm, width, 4) or [text])

    def paintEvent(self, event):
        """每帧只做一次贴图。

        横幅是背景层的上层兄弟：背景动效每秒重画 30 次，横幅会被一起带着重绘。
        描边文字每帧现画要 13ms 上下，扛不住——所以整块字（柔光 + 描边正文 + 出处）
        烘焙成一张位图缓存，只有换句 / 换主题 / 改尺寸才重烘。
        """
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        layer = self._ensure_layer(w, h)
        if layer is None:
            return
        p = QPainter(self)
        p.setOpacity(self._fade)
        p.drawPixmap(0, 0, layer)
        p.end()

    def _layer_token(self, w: int, h: int):
        return (self._quote, w, h, theme.current(), self._light_ink,
                round(float(self.devicePixelRatioF() or 1.0), 2))

    def _ensure_layer(self, w: int, h: int):
        token = self._layer_token(w, h)
        if self._layer is not None and self._layer_token_v == token:
            return self._layer
        self._layer_token_v = token
        dpr = float(self.devicePixelRatioF() or 1.0)
        layer = QPixmap(int(w * dpr), int(h * dpr))
        layer.setDevicePixelRatio(dpr)
        layer.fill(Qt.transparent)
        p = QPainter(layer)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        self._paint_block(p, w, h)
        p.end()
        self._layer = layer
        return layer

    def _paint_block(self, p: QPainter, w: int, h: int) -> None:
        photo = bool(getattr(theme, "HAS_PHOTO", True))
        avail = max(80.0, w - PAD_L - PAD_R)

        px, lines = self._layout_quote(avail)
        qf = self._quote_font(px)
        qfm = QFontMetricsF(qf)
        line_h = qfm.height() * 1.2

        src_font = QFont()
        src_font.setFamilies(QUOTE_FAMILIES
                             + [theme.FONT_FAMILY.split(",")[0].strip('"')])
        src_font.setPixelSize(13)
        sfm = QFontMetricsF(src_font)
        src_h = sfm.height() * 1.35
        src_text = "—— " + self._quote[1]

        block_h = line_h * len(lines) + src_h
        top = max(2.0, (h - block_h) / 2)

        light_ink = self._light_ink
        if photo:
            ink = QColor("#FCFDFE") if light_ink else QColor(theme.TEXT_PRIMARY)
            if light_ink:
                sub_ink = QColor(236, 240, 246)
            else:
                sub_ink = QColor(theme.TEXT_PRIMARY)
                sub_ink.setAlpha(215)
            self._paint_text_scrim(p, h, top, block_h, avail, light_ink)
        else:
            ink = QColor(theme.HERO_INK)
            sub_ink = QColor(theme.HERO_SUB_INK)
        # 描边色永远和字色相反：白字包黑边、黑字包白边
        halo = QColor(2, 4, 10, 165) if light_ink else QColor(255, 255, 255, 215)

        p.setFont(qf)
        y = top
        for ln in lines:
            if photo:
                baseline = y + (line_h + qfm.ascent() - qfm.descent()) / 2
                self._stroked_text(p, ln, qf, PAD_L, baseline, ink, halo)
            else:
                p.setPen(ink)
                p.drawText(QRectF(PAD_L, y, avail, line_h),
                           Qt.AlignLeft | Qt.AlignVCenter, ln)
            y += line_h

        # 出处：右对齐吊在末行下方，与正文右边界对齐
        p.setFont(src_font)
        if photo:
            sx = PAD_L + avail - sfm.horizontalAdvance(src_text)
            sy = y + (src_h + sfm.ascent() - sfm.descent()) / 2
            self._stroked_text(p, src_text, src_font, sx, sy, sub_ink, halo, width=2.6)
        else:
            p.setPen(sub_ink)
            p.drawText(QRectF(PAD_L, y, avail, src_h),
                       Qt.AlignRight | Qt.AlignVCenter, src_text)

    def _stroked_text(self, p: QPainter, text: str, font: QFont, x: float,
                      baseline: float, ink: QColor, halo: QColor,
                      width: float = 2.8) -> None:
        """描边 + 填充地画一行字。

        花花绿绿的照片上，光靠背后糊一块底是压不住的：光晕罩得住左边就罩不住
        右下角的出处。给字包一圈对比色的边，背景再花也认得出，而且不用把照片
        糊掉一大片。字形路径按 (文本, 字号) 缓存，横幅每帧重绘也不会重复构建。
        """
        key = (text, font.pixelSize(), font.families()[0] if font.families() else "")
        cache = getattr(self, "_path_cache", {})
        path = cache.get(key)
        if path is None:
            path = QPainterPath()
            path.addText(0.0, 0.0, font, text)
            if len(cache) > 24:          # 一句最多几行，缓存不会长
                cache.clear()
            cache[key] = path
            self._path_cache = cache
        p.save()
        p.translate(x, baseline)
        pen = QPen(halo, width)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(ink)
        p.drawPath(path)
        p.restore()

    def _paint_text_scrim(self, p: QPainter, h: int, top: float,
                          block_h: float, avail: float, light_ink: bool) -> None:
        base = QColor(6, 8, 14) if light_ink else QColor(255, 255, 255)
        # 深色字压在亮照片上更吃力（叶丛、樱花本身就花），柔光要更实一点
        peak, mid = (96, 42) if light_ink else (110, 52)
        radius = max(260.0, (PAD_L + avail) * 0.62)
        p.save()
        p.translate(0.0, top + block_h / 2)
        p.scale(1.0, (h * 1.05) / max(1.0, radius * 2))   # 压扁成横向椭圆
        g = QRadialGradient(0.0, 0.0, radius)
        g.setColorAt(0.0, QColor(base.red(), base.green(), base.blue(), peak))
        g.setColorAt(0.58, QColor(base.red(), base.green(), base.blue(), mid))
        g.setColorAt(1.0, QColor(base.red(), base.green(), base.blue(), 0))
        p.setBrush(g)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(0.0, 0.0), radius, radius)
        p.restore()
