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
                           QPixmap, QRadialGradient)
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from .. import quotes as quotes_mod
from . import theme

HERO_HEIGHT = 96
ROTATE_MS = 25_000       # 25 秒换一句：够读完，又不至于盯着同一句发呆
FADE_MS = 420
#: 名句左边距。下方页签文字、卡片正文都落在内容列 x=32（body 边距 16 +
#: 页签/卡片 padding 16），横幅这层也从 32 起排才和它们对齐——早先的 24 会让
#: 横幅比正文整整左突出 8px，一眼看着就"歪"。
PAD_L = 32
PAD_R = 96               # 右上角还有个"收起"按钮，别让字顶上去
#: 正文字号阶梯 (字号, 允许行数)：从大往小试，取第一个放得下的
SIZE_LADDER = ((25, 1), (22, 1), (20, 2), (18, 2), (16, 2), (15, 3), (14, 3))
#: 软阴影的偏移环 (dx, dy, alpha)：把染暗的字掩膜按这一圈半透明地叠几次，
#: 就得到一层柔和的下沉投影——主要往下沉 (0,1)/(0,2)，两侧只薄薄托一圈边。
#: 比"给每个字描一圈粗实线"便宜得多（粗笔 stroke 一行长句要 ~20ms），
#: 观感也从"字幕贴纸"变成"有层次的排版"。
SHADOW_RING = ((0, 1, 96), (0, 2, 74), (1, 1, 52), (-1, 1, 52),
               (1, 0, 44), (-1, 0, 44), (0, -1, 34))
#: 名句就用界面字体。试过楷体、宋体，单看是好看，摆进这个界面里和其余所有文字
#: 都不是一路人，横幅像贴了张别处剪来的纸——统一字体反而更整。
def _quote_families():
    return [f.strip().strip('"') for f in theme.FONT_FAMILY.split(",")]


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
        lay.setContentsMargins(0, 0, 10, 6)
        lay.addStretch(1)
        self.collapse_btn = QPushButton("收起", self)
        self.collapse_btn.setObjectName("Ghost")
        self.collapse_btn.setToolTip("收起主题横幅（设置页可再打开）")
        self.collapse_btn.setCursor(Qt.PointingHandCursor)
        self.collapse_btn.setFixedHeight(22)
        self.collapse_btn.clicked.connect(self.collapse_requested)
        # 靠底：右上角要留给贴着横幅顶部画的动效
        lay.addWidget(self.collapse_btn, 0, Qt.AlignBottom)
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
        f.setFamilies(_quote_families())
        f.setPixelSize(px)
        f.setLetterSpacing(QFont.PercentageSpacing, 100)
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
        文字每帧现画（掩膜 + 软阴影合成）扛不住——所以整块字（柔光 + 软阴影正文 +
        出处）烘焙成一张位图缓存，只有换句 / 换主题 / 改尺寸才重烘。
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
        src_font.setFamilies(_quote_families())
        src_font.setPixelSize(13)
        sfm = QFontMetricsF(src_font)
        src_h = sfm.height() * 1.35
        src_text = "—— " + self._quote[1]

        block_h = line_h * len(lines) + src_h
        top = max(2.0, (h - block_h) / 2)

        light_ink = self._light_ink
        # 出处右对齐吊在末行下方，与正文最宽那行的右端对齐——像书里引诗那样
        # 收在诗句尾巴下面，而不是飘到整条横幅的最右端去
        text_right = PAD_L + max(qfm.horizontalAdvance(ln) for ln in lines)
        if photo:
            ink = QColor("#FCFDFE") if light_ink else QColor(theme.TEXT_PRIMARY)
            if light_ink:
                sub_ink = QColor(238, 242, 247)
            else:
                sub_ink = QColor(theme.TEXT_PRIMARY)
                sub_ink.setAlpha(225)
            self._paint_text_scrim(p, h, top, block_h, avail, light_ink)
            self._paint_glyphs(p, w, h, lines, qf, qfm, line_h, top,
                               src_font, sfm, src_h, src_text, text_right,
                               ink, sub_ink, light_ink)
        else:
            p.setFont(qf)
            p.setPen(QColor(theme.HERO_INK))
            y = top
            for ln in lines:
                p.drawText(QRectF(PAD_L, y, avail, line_h),
                           Qt.AlignLeft | Qt.AlignVCenter, ln)
                y += line_h
            p.setFont(src_font)
            p.setPen(QColor(theme.HERO_SUB_INK))
            p.drawText(QRectF(PAD_L, y, text_right - PAD_L, src_h),
                       Qt.AlignRight | Qt.AlignVCenter, src_text)

    def _paint_glyphs(self, p: QPainter, w: int, h: int, lines: List[str],
                      qf: QFont, qfm: QFontMetricsF, line_h: float, top: float,
                      src_font: QFont, sfm: QFontMetricsF, src_h: float,
                      src_text: str, text_right: float, ink: QColor,
                      sub_ink: QColor, light_ink: bool) -> None:
        """照片主题下，用柔和投影而非硬描边把字压在照片上。

        花花绿绿的照片上光靠背后糊一块底压不住，早先给每个字包一圈实心对比色边，
        读起来却像字幕贴纸——和界面其余排版不是一路人。这里换成真正的投影：把整块
        字（正文 + 出处）先画进一张掩膜位图，用 SourceIn 染成暗/亮色做阴影母版，再按
        `SHADOW_RING` 半透明地偏移叠几次得到柔和下沉的影子，最后盖上清晰正文。

        全程只是位图填充与贴图（没有粗笔 stroke），bake 一次 ~4ms，比逐字描边还快；
        且这一切都发生在 `_ensure_layer` 烘焙缓存图时，`paintEvent` 逐帧照旧只贴一张图。
        """
        # 1) 整块字画进掩膜（正文用 ink 色、出处用 sub_ink 色，各自的不透明度也带上）
        mask = QPixmap(int(w), int(h))
        mask.fill(Qt.transparent)
        mp = QPainter(mask)
        mp.setRenderHint(QPainter.Antialiasing, True)
        mp.setPen(Qt.NoPen)
        y = top
        for ln in lines:
            baseline = y + (line_h + qfm.ascent() - qfm.descent()) / 2
            path = QPainterPath()
            path.addText(PAD_L, baseline, qf, ln)
            mp.setBrush(ink)
            mp.drawPath(path)
            y += line_h
        sx = max(float(PAD_L), text_right - sfm.horizontalAdvance(src_text))
        sy = y + (src_h + sfm.ascent() - sfm.descent()) / 2
        src_path = QPainterPath()
        src_path.addText(sx, sy, src_font, src_text)
        mp.setBrush(sub_ink)
        mp.drawPath(src_path)
        mp.end()

        # 2) 由掩膜染出阴影母版：白字配暗影、黑字配亮影
        shadow = QPixmap(mask.size())
        shadow.fill(Qt.transparent)
        sp = QPainter(shadow)
        sp.drawPixmap(0, 0, mask)
        sp.setCompositionMode(QPainter.CompositionMode_SourceIn)
        sp.fillRect(shadow.rect(),
                    QColor(2, 4, 10) if light_ink else QColor(255, 255, 255))
        sp.end()

        # 3) 偏移叠加成软阴影，再盖上清晰正文
        for dx, dy, alpha in SHADOW_RING:
            p.setOpacity(alpha / 255.0)
            p.drawPixmap(dx, dy, shadow)
        p.setOpacity(1.0)
        p.drawPixmap(0, 0, mask)

    def _paint_text_scrim(self, p: QPainter, h: int, top: float,
                          block_h: float, avail: float, light_ink: bool) -> None:
        base = QColor(6, 8, 14) if light_ink else QColor(255, 255, 255)
        # 可读性现在主要靠字自带的软阴影，这层柔光只做辅助——压薄一点，
        # 免得在叶丛、樱花这种亮照片上糊出一团看得见的"灰云"。
        # 深色字压在亮照片上更吃力，仍留一点点更实
        peak, mid = (56, 24) if light_ink else (66, 30)
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
