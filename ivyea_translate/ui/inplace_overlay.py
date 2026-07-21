"""原位翻译覆盖层：把译文贴回每段原文所在的位置。

与弹窗式截图翻译的区别是"不打断视线"：框选完，屏幕上那段外文原地变成中文，
读完按 Esc 就走。实现要点：

坐标链路（本功能最容易错的地方）——
    OCR box(可能是 ×2 放大图) --÷scale--> 裁剪图物理像素 --÷dpr--> 选区内逻辑坐标
scale 已在 ocr.recognize_blocks 里消掉，这里只处理 dpr（block_rect 是纯函数，可单测）。

毛玻璃底不是实时模糊——Qt 拿不到自己窗口背后的画面。但框选那一刻的截图就在手上，
把它高斯模糊一次当卡片底纹，效果等价且零开销。

悬停某块时该块不绘制：覆盖层背后就是真实屏幕，不画即"淡出"露出原文，
不需要另存一份原图。
"""
from __future__ import annotations

import logging
from typing import List, Optional, Sequence, Tuple

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QWidget

from . import theme

log = logging.getLogger(__name__)

CARD_RADIUS = 8
CARD_PAD_X, CARD_PAD_Y = 7, 4
CARD_ALPHA = 214          # 白色卡片不透明度（毛玻璃底透出来一点）
BLUR_RADIUS = 9
MIN_FONT_PX, MAX_FONT_PX = 9, 40
EXTRA_ROOM = 96           # 窗口比选区多留的余量，字放不下时卡片能往下/右溢出


def block_rect(x: float, y: float, w: float, h: float, dpr: float,
               pad: int = 3) -> QRect:
    """物理像素包围框 -> 覆盖层内逻辑坐标（纯函数，可单测）。"""
    d = dpr if dpr and dpr > 0 else 1.0
    left = int(round(x / d)) - pad
    top = int(round(y / d)) - pad
    width = int(round(w / d)) + 2 * pad
    height = int(round(h / d)) + 2 * pad
    return QRect(max(0, left), max(0, top), max(1, width), max(1, height))


def fit_font_px(text: str, width: int, height: int, start_px: int,
                family: str = "") -> int:
    """在给定框内找放得下的最大字号（纯函数式；只读 QFontMetrics）。"""
    start = max(MIN_FONT_PX, min(int(start_px), MAX_FONT_PX))
    inner_w = max(10, width - 2 * CARD_PAD_X)
    inner_h = max(8, height - 2 * CARD_PAD_Y)
    for px in range(start, MIN_FONT_PX - 1, -1):
        font = QFont(family) if family else QFont()
        font.setPixelSize(px)
        rect = QFontMetrics(font).boundingRect(
            QRect(0, 0, inner_w, 10000), Qt.TextWordWrap, text)
        if rect.height() <= inner_h:
            return px
    return MIN_FONT_PX


def text_block_height(text: str, width: int, px: int, family: str = "") -> int:
    """按给定字号排版后需要的卡片高度（含内边距）。"""
    font = QFont(family) if family else QFont()
    font.setPixelSize(px)
    inner_w = max(10, width - 2 * CARD_PAD_X)
    rect = QFontMetrics(font).boundingRect(
        QRect(0, 0, inner_w, 10000), Qt.TextWordWrap, text)
    return rect.height() + 2 * CARD_PAD_Y


def _blur_pixmap(shot: QPixmap, radius: int = BLUR_RADIUS) -> Optional[QPixmap]:
    """高斯模糊整张选区图，失败返回 None（降级为纯白卡片，不影响可用性）。"""
    try:
        from PIL import Image, ImageFilter

        img = shot.toImage().convertToFormat(QImage.Format_RGBA8888)
        w, h = img.width(), img.height()
        if w <= 0 or h <= 0:
            return None
        pil = Image.frombuffer(
            "RGBA", (w, h), bytes(img.constBits()), "raw", "RGBA",
            img.bytesPerLine(), 1,
        ).filter(ImageFilter.GaussianBlur(radius))
        data = pil.tobytes("raw", "RGBA")
        out = QImage(data, w, h, w * 4, QImage.Format_RGBA8888).copy()
        return QPixmap.fromImage(out)
    except Exception as e:  # PIL 缺失/图像异常都不该让功能挂掉
        log.info("毛玻璃底生成失败，降级为纯色卡片：%s", e)
        return None


class InPlaceOverlay(QWidget):
    """一次原位翻译一个实例；Esc 或点击任意处关闭。"""

    closed = Signal()

    def __init__(self, region: QRect, shot: QPixmap, dpr: float):
        super().__init__(None)
        self._region = QRect(region)
        self._dpr = dpr if dpr and dpr > 0 else 1.0
        self._blurred = _blur_pixmap(shot)
        self._status: Optional[str] = "正在识别文字…"
        self._cards: List[Tuple[QRect, str, int]] = []   # 卡片框, 译文, 字号
        self._hover = -1

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setMouseTracking(True)
        self.setCursor(Qt.ArrowCursor)
        self.setGeometry(self._window_geometry())

    def _window_geometry(self) -> QRect:
        """窗口 = 选区 + 右下余量（译文比原文长时卡片要能往外长），夹回屏幕。"""
        screen = (QGuiApplication.screenAt(self._region.center())
                  or QGuiApplication.primaryScreen())
        avail = screen.availableGeometry() if screen else self._region
        geo = QRect(self._region)
        geo.setWidth(min(geo.width() + EXTRA_ROOM,
                         max(geo.width(), avail.right() - geo.left() + 1)))
        geo.setHeight(min(geo.height() + EXTRA_ROOM,
                          max(geo.height(), avail.bottom() - geo.top() + 1)))
        return geo

    def start(self) -> None:
        self.show()
        self.raise_()

    # ---- 状态 ----

    def set_status(self, text: str) -> None:
        self._status = text
        self.update()

    def fail(self, message: str, auto_close_ms: int = 2000) -> None:
        self._status = message
        self._cards = []
        self.update()
        QTimer.singleShot(auto_close_ms, self.close)

    def set_blocks(self, blocks: Sequence, translations: Sequence[str]) -> None:
        """把译文按块贴回原位。blocks 为 ocr.OcrBlock（物理像素坐标）。"""
        cards: List[Tuple[QRect, str, int]] = []
        for block, text in zip(blocks, translations):
            text = (text or "").strip()
            if not text:
                continue
            rect = block_rect(block.x, block.y, block.w, block.h, self._dpr)
            # 初始字号照着原文行高走，读起来和原文一样大
            start_px = int(max(MIN_FONT_PX, (block.line_h or block.h) / self._dpr * 0.82))
            px = fit_font_px(text, rect.width(), rect.height(), start_px, theme.FONT_FAMILY)
            need_h = text_block_height(text, rect.width(), px, theme.FONT_FAMILY)
            if need_h > rect.height():
                # 到最小字号仍放不下：卡片往下长，夹在窗口内
                rect.setHeight(min(need_h, self.height() - rect.top()))
            cards.append((rect, text, px))
        self._cards = cards
        self._status = None if cards else "没有识别到文字"
        self.update()
        if not cards:
            QTimer.singleShot(1500, self.close)

    # ---- 绘制 ----

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        if self._status is not None:
            self._paint_status(p)
        for idx, (rect, text, px) in enumerate(self._cards):
            if idx == self._hover:
                continue  # 悬停：不画这块，露出屏幕上的真实原文
            self._paint_card(p, rect, text, px)
        p.end()

    def _paint_status(self, p: QPainter) -> None:
        font = QFont(theme.FONT_FAMILY)
        font.setPixelSize(13)
        p.setFont(font)
        fm = QFontMetrics(font)
        w = fm.horizontalAdvance(self._status) + 28
        h = fm.height() + 14
        rect = QRect(0, 0, w, h)
        rect.moveCenter(QPoint(self._region.width() // 2, self._region.height() // 2))
        self._paint_glass(p, rect)
        p.setPen(QColor(theme.TEXT_PRIMARY))
        p.drawText(rect, Qt.AlignCenter, self._status)

    def _paint_glass(self, p: QPainter, rect: QRect) -> None:
        """毛玻璃托底：模糊截图 + 白色半透明 + 品牌绿细描边。"""
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), CARD_RADIUS, CARD_RADIUS)
        if self._blurred is not None:
            src = QRectF(rect.x() * self._dpr, rect.y() * self._dpr,
                         rect.width() * self._dpr, rect.height() * self._dpr)
            p.save()
            p.setClipPath(path)
            p.drawPixmap(QRectF(rect), self._blurred, src)
            p.restore()
        p.fillPath(path, QColor(255, 255, 255, CARD_ALPHA))
        pen = QPen(QColor(107, 165, 63, 120), 1)  # theme.ACCENT，淡一档
        p.setPen(pen)
        p.drawPath(path)

    def _paint_card(self, p: QPainter, rect: QRect, text: str, px: int) -> None:
        self._paint_glass(p, rect)
        font = QFont(theme.FONT_FAMILY)
        font.setPixelSize(px)
        p.setFont(font)
        p.setPen(QColor(theme.TEXT_PRIMARY))
        inner = rect.adjusted(CARD_PAD_X, CARD_PAD_Y, -CARD_PAD_X, -CARD_PAD_Y)
        p.drawText(inner, Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignVCenter, text)

    # ---- 交互 ----

    def _card_at(self, pos: QPoint) -> int:
        for idx, (rect, _t, _px) in enumerate(self._cards):
            if rect.contains(pos):
                return idx
        return -1

    def mouseMoveEvent(self, event):
        idx = self._card_at(event.position().toPoint())
        if idx != self._hover:
            self._hover = idx
            self.setToolTip("松开鼠标移开即恢复译文" if idx >= 0 else "")
            self.update()

    def mousePressEvent(self, event):
        self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)
