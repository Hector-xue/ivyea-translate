"""原位翻译覆盖层：把译文贴回每段原文所在的位置。

与弹窗式截图翻译的区别是"不打断视线"：框选完，屏幕上那段外文原地变成中文，
读完按 Esc 就走。实现要点：

坐标链路（本功能最容易错的地方）——
    OCR box(可能是 ×2 放大图) --÷scale--> 裁剪图物理像素 --÷dpr--> 选区内逻辑坐标
scale 已在 ocr.recognize_blocks 里消掉，这里只处理 dpr（block_rect 是纯函数，可单测）。

毛玻璃底不是实时模糊——Qt 拿不到自己窗口背后的画面。但框选那一刻的截图就在手上，
把它高斯模糊一次当卡片底纹，效果等价且零开销。

必须能关掉（v0.25.0 踩过）：这是一扇置顶、无边框、盖在别人窗口上的窗，
用户找不到出口就等于把屏幕黏死了。所以三条退路都要有——可见的 ✕ 按钮、
Esc（窗口必须真的拿到键盘焦点，否则按键根本到不了它）、点击任意处。
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
CARD_PAD_X, CARD_PAD_Y = 8, 5
CARD_ALPHA = 232          # 卡片白底：要盖住原文，又让模糊底透出一点质感
BLUR_RADIUS = 10
MIN_FONT_PX, MAX_FONT_PX = 11, 40
CLOSE_BTN = 22            # 右上角关闭按钮边长
HINT_MS = 2600            # "Esc 关闭"提示显示时长


def block_rect(x: float, y: float, w: float, h: float, dpr: float,
               pad: int = 3) -> QRect:
    """物理像素包围框 -> 覆盖层内逻辑坐标（纯函数，可单测）。"""
    d = dpr if dpr and dpr > 0 else 1.0
    left = int(round(x / d)) - pad
    top = int(round(y / d)) - pad
    width = int(round(w / d)) + 2 * pad
    height = int(round(h / d)) + 2 * pad
    return QRect(max(0, left), max(0, top), max(1, width), max(1, height))


def text_size(text: str, width: int, px: int, family: str = "") -> Tuple[int, int]:
    """按给定字号排版后需要的（宽, 高），含卡片内边距。"""
    font = QFont(family) if family else QFont()
    font.setPixelSize(px)
    inner_w = max(10, width - 2 * CARD_PAD_X)
    rect = QFontMetrics(font).boundingRect(
        QRect(0, 0, inner_w, 10000), Qt.TextWordWrap, text)
    return rect.width() + 2 * CARD_PAD_X, rect.height() + 2 * CARD_PAD_Y


def fit_font_px(text: str, width: int, height: int, start_px: int,
                family: str = "") -> int:
    """在给定框内找放得下的最大字号（纯函数式；只读 QFontMetrics）。"""
    start = max(MIN_FONT_PX, min(int(start_px), MAX_FONT_PX))
    for px in range(start, MIN_FONT_PX - 1, -1):
        if text_size(text, width, px, family)[1] <= height:
            return px
    return MIN_FONT_PX


def layout_card(text: str, base: QRect, bounds: QRect, start_px: int,
                family: str = "") -> Tuple[QRect, int]:
    """给一块译文算出卡片矩形和字号（纯函数，可单测）。

    译文往往比原文长，硬塞回原框只能把字压成蚂蚁。所以先按原文字号试；放不下就
    在 bounds 内先往右加宽（读起来最自然），仍不够再往下加高，最后才降字号。
    """
    rect = QRect(base)
    px = max(MIN_FONT_PX, min(int(start_px), MAX_FONT_PX))
    room_right = max(rect.width(), bounds.right() - rect.left() + 1)
    room_down = max(rect.height(), bounds.bottom() - rect.top() + 1)

    _w, need_h = text_size(text, rect.width(), px, family)
    if need_h > rect.height() and rect.width() < room_right:
        # 逐档加宽；只有真能把高度降下来才留住这次加宽——否则差的那点高度是
        # 内边距造成的，加宽白白把卡片撑到原文之外
        for factor in (1.4, 1.9, 2.6, 3.5):
            wide = min(room_right, int(base.width() * factor))
            if wide <= rect.width():
                break
            _w2, h2 = text_size(text, wide, px, family)
            if h2 >= need_h:
                break
            rect.setWidth(wide)
            need_h = h2
            if need_h <= rect.height():
                break
    if need_h > rect.height():
        rect.setHeight(min(need_h, room_down))
        _w, need_h = text_size(text, rect.width(), px, family)
    if need_h > rect.height():
        px = fit_font_px(text, rect.width(), rect.height(), px, family)
    return rect, px


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
    """一次原位翻译一个实例；✕ / Esc / 点击任意处 都能关闭。"""

    closed = Signal()

    def __init__(self, region: QRect, shot: QPixmap, dpr: float):
        super().__init__(None)
        self._region = QRect(region)
        self._dpr = dpr if dpr and dpr > 0 else 1.0
        self._blurred = _blur_pixmap(shot)
        self._status: Optional[str] = "正在识别文字…"
        self._blocks: List = []
        self._cards: List[Optional[Tuple[QRect, str, int]]] = []
        self._hover = -1
        self._show_hint = True

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.StrongFocus)   # 不接受焦点 = Esc 永远收不到
        self.setMouseTracking(True)
        self.setCursor(Qt.ArrowCursor)
        # 窗口就是选区本身：多留的透明边会白白吃掉选区外的点击
        self.setGeometry(self._region)
        QTimer.singleShot(HINT_MS, self._drop_hint)

    def start(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()   # 真正拿到键盘焦点，Esc 才有用
        self.setFocus(Qt.OtherFocusReason)

    def _drop_hint(self) -> None:
        if self._show_hint:
            self._show_hint = False
            self.update()

    # ---- 状态 ----

    def set_status(self, text: str) -> None:
        self._status = text
        self.update()

    def fail(self, message: str, auto_close_ms: int = 2500) -> None:
        self._status = message
        self._cards = []
        self.update()
        QTimer.singleShot(auto_close_ms, self.close)

    def prepare(self, blocks: Sequence) -> None:
        """OCR 完成：记下每块的位置，等译文逐块回填。"""
        self._blocks = list(blocks)
        self._cards = [None] * len(self._blocks)
        if not self._blocks:
            self.fail("没有识别到文字", 1500)
            return
        self._status = "翻译中…"
        self.update()

    def set_block_text(self, index: int, text: str) -> None:
        """回填第 index 块的译文（逐块到达，翻一块显示一块）。"""
        if not (0 <= index < len(self._blocks)):
            return
        text = (text or "").strip()
        if not text:
            return
        block = self._blocks[index]
        base = block_rect(block.x, block.y, block.w, block.h, self._dpr)
        bounds = QRect(0, 0, self.width(), self.height())
        start_px = int(max(MIN_FONT_PX, (block.line_h or block.h) / self._dpr * 0.86))
        rect, px = layout_card(text, base, bounds, start_px, theme.FONT_FAMILY)
        self._cards[index] = (rect, text, px)
        self._status = None
        self.update()

    def finish(self) -> None:
        # 一张卡都没有时保留状态文案（"没有识别到文字"之类），否则就成了空窗
        if any(card is not None for card in self._cards):
            self._status = None
        self.update()

    # 兼容旧调用（一次性给全部译文）
    def set_blocks(self, blocks: Sequence, translations: Sequence[str]) -> None:
        self.prepare(blocks)
        for i, text in enumerate(translations):
            self.set_block_text(i, text)
        self.finish()

    # ---- 绘制 ----

    def _close_rect(self) -> QRect:
        return QRect(self.width() - CLOSE_BTN - 6, 6, CLOSE_BTN, CLOSE_BTN)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        for idx, card in enumerate(self._cards):
            if card is None or idx == self._hover:
                continue  # 悬停：不画这块，露出屏幕上的真实原文
            rect, text, px = card
            self._paint_card(p, rect, text, px)
        if self._status is not None:
            self._paint_status(p)
        self._paint_close(p)
        if self._show_hint:
            self._paint_hint(p)
        p.end()

    def _paint_status(self, p: QPainter) -> None:
        font = QFont(theme.FONT_FAMILY)
        font.setPixelSize(13)
        p.setFont(font)
        fm = QFontMetrics(font)
        rect = QRect(0, 0, fm.horizontalAdvance(self._status) + 28, fm.height() + 14)
        rect.moveCenter(QPoint(self.width() // 2, self.height() // 2))
        self._paint_glass(p, rect)
        p.setPen(QColor(theme.TEXT_PRIMARY))
        p.drawText(rect, Qt.AlignCenter, self._status)

    def _paint_hint(self, p: QPainter) -> None:
        """开头几秒告诉用户怎么退出——置顶窗最怕的就是"关不掉"。"""
        font = QFont(theme.FONT_FAMILY)
        font.setPixelSize(11)
        p.setFont(font)
        fm = QFontMetrics(font)
        text = "Esc 关闭 · 悬停看原文"
        rect = self._least_covering_corner(
            fm.horizontalAdvance(text) + 18, fm.height() + 8)
        if rect is None:
            return  # 四角都压着译文：宁可不提示，也不挡内容（✕ 一直都在）
        self._paint_glass(p, rect)
        p.setPen(QColor(theme.TEXT_SECONDARY))
        p.drawText(rect, Qt.AlignCenter, text)

    def _least_covering_corner(self, w: int, h: int, margin: int = 6) -> Optional[QRect]:
        """挑一个不压译文的角放提示；四角都被占就返回 None。

        （右上角留给关闭按钮，不参与竞争。）
        """
        candidates = [
            QRect(margin, self.height() - h - margin, w, h),                 # 左下
            QRect(self.width() - w - margin, self.height() - h - margin, w, h),  # 右下
            QRect(margin, margin, w, h),                                     # 左上
        ]
        for rect in candidates:
            clear = all(
                card is None or not rect.intersects(card[0])
                for card in self._cards
            )
            if clear:
                return rect
        return None

    def _paint_close(self, p: QPainter) -> None:
        rect = self._close_rect()
        self._paint_glass(p, rect)
        pen = QPen(QColor(theme.TEXT_SECONDARY), 1.4)
        p.setPen(pen)
        m = 7
        p.drawLine(rect.left() + m, rect.top() + m, rect.right() - m, rect.bottom() - m)
        p.drawLine(rect.right() - m, rect.top() + m, rect.left() + m, rect.bottom() - m)

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
        p.setPen(QPen(QColor(107, 165, 63, 130), 1))  # theme.ACCENT，淡一档
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
        for idx, card in enumerate(self._cards):
            if card is not None and card[0].contains(pos):
                return idx
        return -1

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        self.setCursor(Qt.PointingHandCursor if self._close_rect().contains(pos)
                       else Qt.ArrowCursor)
        idx = self._card_at(pos)
        if idx != self._hover:
            self._hover = idx
            self.update()

    def mousePressEvent(self, event):
        self.close()   # ✕ 也好、空白处也好，点一下就走

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Space):
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)
