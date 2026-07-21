"""原位翻译覆盖层：把译文贴回每段原文所在的位置。

与弹窗式截图翻译的区别是"不打断视线"：框选完，屏幕上那段外文原地变成中文，
读完按 Esc 就走。实现要点：

坐标链路（本功能最容易错的地方）——
    OCR box(可能是 ×2 放大图) --÷scale--> 裁剪图物理像素 --÷dpr--> 选区内逻辑坐标
scale 已在 ocr.recognize_blocks 里消掉，这里只处理 dpr（block_rect 是纯函数，可单测）。
窗口比选区多出一条工具条（选区下方放不下就翻到上方；选区太窄就向右拓宽窗口），
_cards 里的矩形永远是"选区内坐标"，绘制统一经 (offset_x, content_top) 平移。

卡片底色不是毛玻璃而是"贴回原图"（Google Lens 式）：对每块译文取样底图边缘
环带的平均色做纯色底、按亮度自动黑/白文字。块与块因各取邻近底色，视觉上融进
原图；选区整体描一圈品牌绿外框 + 左上角小角标，用户一眼知道"这一层是翻译"。

必须能关掉（v0.25.0 踩过）：这是一扇置顶、无边框、盖在别人窗口上的窗，
用户找不到出口就等于把屏幕黏死了。退路——工具条 ✕、Esc、点击空白处、
切到别的窗口（WindowDeactivate）都能关。工具条只用按钮不用 QMenu：
菜单一弹会触发 WindowDeactivate，把自己关了。
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, QTimer, QVariantAnimation, Signal
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
from PySide6.QtWidgets import QApplication, QHBoxLayout, QPushButton, QWidget

from . import theme

CARD_RADIUS = 4           # 贴片圆角：小，更像"原文换了种文字"
PILL_RADIUS = 8           # 状态条/提示条圆角
CARD_PAD_X, CARD_PAD_Y = 8, 5
MIN_FONT_PX, MAX_FONT_PX = 11, 40
HINT_MS = 2600            # "Esc 关闭"提示显示时长
TOOLBAR_GAP = 8           # 选区与工具条的间距
INK_DARK = "#1F2937"      # 浅底上的文字色
INK_LIGHT = "#F8FAF5"     # 深底上的文字色
FRAME_ALPHA_IDLE = 200    # 选区外框描边透明度（静止态）
BREATH_MS = 1600          # loading 边框呼吸一个周期


def block_rect(x: float, y: float, w: float, h: float, dpr: float,
               pad: int = 3) -> QRect:
    """物理像素包围框 -> 选区内逻辑坐标（纯函数，可单测）。"""
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


def sample_bg_color(img: QImage, rect: QRect, ring: int = 3) -> QColor:
    """取矩形四边 ring 像素环带的平均色（物理像素坐标；纯函数，可单测）。

    只采边缘不采中心：中心是文字笔画，采进去底色会发灰。越界自动夹回图内，
    取不到任何样本时退回白色。
    """
    r = rect.intersected(QRect(0, 0, img.width(), img.height()))
    if r.isEmpty():
        return QColor(255, 255, 255)
    band_h = min(ring, r.height())
    band_w = min(ring, r.width())
    bands = [
        QRect(r.left(), r.top(), r.width(), band_h),                              # 上
        QRect(r.left(), max(r.top(), r.bottom() - band_h + 1), r.width(), band_h),  # 下
        QRect(r.left(), r.top(), band_w, r.height()),                             # 左
        QRect(max(r.left(), r.right() - band_w + 1), r.top(), band_w, r.height()),  # 右
    ]
    total_r = total_g = total_b = count = 0
    for band in bands:
        for yy in range(band.top(), band.bottom() + 1):
            for xx in range(band.left(), band.right() + 1):
                c = img.pixelColor(xx, yy)
                total_r += c.red()
                total_g += c.green()
                total_b += c.blue()
                count += 1
    if not count:
        return QColor(255, 255, 255)
    return QColor(total_r // count, total_g // count, total_b // count)


def ink_for(bg: QColor) -> QColor:
    """按底色相对亮度选文字色：浅底近黑、深底近白（纯函数，可单测）。"""
    lum = (0.2126 * bg.red() + 0.7152 * bg.green() + 0.0722 * bg.blue()) / 255.0
    return QColor(INK_DARK) if lum > 0.55 else QColor(INK_LIGHT)


class OverlayToolbar(QWidget):
    """覆盖层的迷你工具条：复制译文 | 复制原文 | 原文 | 弹窗 | ✕。"""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("OverlayBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            f"""
            QWidget#OverlayBar {{
                background: rgba(255, 255, 255, 0.97);
                border: 1px solid {theme.CARD_BORDER};
                border-radius: 10px;
            }}
            QPushButton {{
                background: transparent;
                border: none;
                border-radius: 7px;
                padding: 4px 9px;
                font-size: 12px;
                color: {theme.TEXT_SECONDARY};
            }}
            QPushButton:hover {{ background: {theme.ACCENT_SOFT}; color: {theme.ACCENT_HOVER}; }}
            QPushButton:checked {{ background: {theme.ACCENT_SOFT}; color: {theme.ACCENT_HOVER}; }}
            QPushButton:disabled {{ color: {theme.TEXT_MUTED}; }}
            """
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 3, 6, 3)
        lay.setSpacing(2)
        self.btn_copy_res = QPushButton("复制译文")
        self.btn_copy_src = QPushButton("复制原文")
        self.btn_orig = QPushButton("原文")
        self.btn_orig.setCheckable(True)
        self.btn_orig.setToolTip("整体切回屏幕上的原文（再点一下回到译文）")
        self.btn_popup = QPushButton("弹窗")
        self.btn_popup.setToolTip("转成原文/译文对照弹窗")
        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedWidth(26)
        for b in (self.btn_copy_res, self.btn_copy_src, self.btn_orig,
                  self.btn_popup, self.btn_close):
            b.setFocusPolicy(Qt.NoFocus)  # 焦点留给覆盖层本体，Esc 才一直有效
            lay.addWidget(b)
        # OCR/翻译没完成前没东西可复制/可转
        self.btn_copy_res.setEnabled(False)
        self.btn_copy_src.setEnabled(False)
        self.btn_popup.setEnabled(False)


class InPlaceOverlay(QWidget):
    """一次原位翻译一个实例；工具条 ✕ / Esc / 点空白 / 切窗口 都能关闭。"""

    closed = Signal()
    popup_requested = Signal(str, str, QRect)  # 原文, 译文, 选区（全局坐标）

    def __init__(self, region: QRect, shot: QPixmap, dpr: float):
        super().__init__(None)
        self._region = QRect(region)
        self._dpr = dpr if dpr and dpr > 0 else 1.0
        self._shot_img = shot.toImage()
        self._status: Optional[str] = "正在识别文字…"
        self._blocks: List = []
        self._cards: List[Optional[Tuple[QRect, str, int, QColor, QColor]]] = []
        self._show_original = False
        self._show_hint = True
        self._frame_alpha = FRAME_ALPHA_IDLE

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.StrongFocus)   # 不接受焦点 = Esc 永远收不到
        self.setCursor(Qt.ArrowCursor)

        self._toolbar = OverlayToolbar(self)
        self._toolbar.btn_copy_res.clicked.connect(self._copy_translation)
        self._toolbar.btn_copy_src.clicked.connect(self._copy_original)
        self._toolbar.btn_orig.toggled.connect(self._set_show_original)
        self._toolbar.btn_popup.clicked.connect(self._to_popup)
        self._toolbar.btn_close.clicked.connect(self.close)
        self._toolbar.adjustSize()
        self._apply_geometry()

        # loading 态：选区外框呼吸（alpha 90↔220），比中央转圈更"活"也不挡内容
        self._breath = QVariantAnimation(self)
        self._breath.setStartValue(0.0)
        self._breath.setEndValue(1.0)
        self._breath.setDuration(BREATH_MS)
        self._breath.setLoopCount(-1)
        self._breath.valueChanged.connect(self._on_breath)
        self._breath.start()

        QTimer.singleShot(HINT_MS, self._drop_hint)

    def _apply_geometry(self) -> None:
        """窗口 = 选区 + 一条工具条。下方放不下就翻到上方；选区太窄就向右拓宽
        窗口装下工具条（外框仍只描选区，多出来的部分全透明）。"""
        tb = self._toolbar
        ext = tb.height() + TOOLBAR_GAP
        screen = (QGuiApplication.screenAt(self._region.center())
                  or QGuiApplication.primaryScreen())
        avail = screen.availableGeometry() if screen else QRect(0, 0, 10 ** 6, 10 ** 6)
        below = self._region.bottom() + ext <= avail.bottom()
        self._content_top = 0 if below else ext
        win_w = max(self._region.width(), tb.width())
        win_x = self._region.x()
        if win_x + win_w - 1 > avail.right():
            win_x = max(avail.left(), avail.right() - win_w + 1)
        self._offset_x = self._region.x() - win_x
        win_y = self._region.y() - self._content_top
        self.setGeometry(win_x, win_y, win_w, self._region.height() + ext)
        tx = self._offset_x + (self._region.width() - tb.width()) // 2
        tx = max(0, min(tx, win_w - tb.width()))
        ty = self._region.height() + TOOLBAR_GAP if below else 0
        tb.move(tx, ty)

    def _selection_rect(self) -> QRect:
        """选区在窗口坐标系里的位置。"""
        return QRect(self._offset_x, self._content_top,
                     self._region.width(), self._region.height())

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

    def _set_loading(self, on: bool) -> None:
        if on:
            if self._breath.state() != QVariantAnimation.Running:
                self._breath.start()
            return
        self._breath.stop()
        self._frame_alpha = FRAME_ALPHA_IDLE
        self.update()

    def _on_breath(self, v: float) -> None:
        self._frame_alpha = 90 + int((220 - 90) * 0.5 * (1 - math.cos(2 * math.pi * v)))
        self.update()

    def set_status(self, text: str) -> None:
        self._status = text
        self.update()

    def fail(self, message: str, auto_close_ms: int = 2500) -> None:
        self._status = message
        self._cards = []
        self._set_loading(False)
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
        self._toolbar.btn_copy_src.setEnabled(True)  # 原文此刻已到手
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
        bounds = QRect(0, 0, self._region.width(), self._region.height())
        start_px = int(max(MIN_FONT_PX, (block.line_h or block.h) / self._dpr * 0.86))
        rect, px = layout_card(text, base, bounds, start_px, theme.FONT_FAMILY)
        phys = QRect(int(rect.x() * self._dpr), int(rect.y() * self._dpr),
                     int(rect.width() * self._dpr), int(rect.height() * self._dpr))
        bg = sample_bg_color(self._shot_img, phys)
        self._cards[index] = (rect, text, px, bg, ink_for(bg))
        self._status = None
        self.update()

    def finish(self) -> None:
        # 一张卡都没有时保留状态文案（"没有识别到文字"之类），否则就成了空窗
        if any(card is not None for card in self._cards):
            self._status = None
            self._toolbar.btn_copy_res.setEnabled(True)
            self._toolbar.btn_popup.setEnabled(True)
        self._set_loading(False)
        self.update()

    # 兼容旧调用（一次性给全部译文）
    def set_blocks(self, blocks: Sequence, translations: Sequence[str]) -> None:
        self.prepare(blocks)
        for i, text in enumerate(translations):
            self.set_block_text(i, text)
        self.finish()

    # ---- 复制 / 转弹窗 ----

    def translations_text(self) -> str:
        return "\n\n".join(c[1] for c in self._cards if c is not None)

    def originals_text(self) -> str:
        return "\n\n".join(b.text for b in self._blocks)

    def _copy(self, text: str, btn: QPushButton) -> None:
        if not text:
            return
        app = QApplication.instance()
        if app and hasattr(app, "mark_own_copy"):
            app.mark_own_copy(text)  # 防止自家写入触发 Ctrl+C+C 划词翻译
        QGuiApplication.clipboard().setText(text)
        old = btn.text()
        btn.setText("已复制")
        QTimer.singleShot(1200, btn, lambda: btn.setText(old))

    def _copy_translation(self) -> None:
        self._copy(self.translations_text(), self._toolbar.btn_copy_res)

    def _copy_original(self) -> None:
        self._copy(self.originals_text(), self._toolbar.btn_copy_src)

    def _set_show_original(self, on: bool) -> None:
        self._show_original = on
        self.update()

    def _to_popup(self) -> None:
        self.popup_requested.emit(
            self.originals_text(), self.translations_text(), QRect(self._region))

    # ---- 绘制 ----

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        sel = self._selection_rect()
        p.save()
        p.translate(sel.topLeft())
        if not self._show_original:
            for card in self._cards:
                if card is not None:
                    self._paint_card(p, *card)
        if self._status is not None:
            self._paint_status(p)
        if self._show_hint:
            self._paint_hint(p)
        p.restore()
        self._paint_frame(p, sel)
        self._paint_badge(p, sel)
        p.end()

    def _paint_frame(self, p: QPainter, sel: QRect) -> None:
        """选区外框：静止时细描，loading 时随呼吸动画明暗。"""
        p.setPen(QPen(QColor(107, 165, 63, self._frame_alpha), 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(sel).adjusted(0.75, 0.75, -0.75, -0.75), 6, 6)

    def _paint_badge(self, p: QPainter, sel: QRect) -> None:
        """左上角品牌小角标：告诉用户"这一层是翻译"。太小的选区不画，别喧宾夺主。"""
        if sel.width() < 140 or sel.height() < 48:
            return
        font = QFont(theme.FONT_FAMILY)
        font.setPixelSize(10)
        font.setBold(True)
        fm = QFontMetrics(font)
        text = "Ivyea 译"
        rect = QRect(sel.x() + 6, sel.y() + 6,
                     fm.horizontalAdvance(text) + 14, fm.height() + 6)
        self._paint_glass(p, rect)
        p.setFont(font)
        p.setPen(QColor(theme.ACCENT))
        p.drawText(rect, Qt.AlignCenter, text)

    def _paint_status(self, p: QPainter) -> None:
        font = QFont(theme.FONT_FAMILY)
        font.setPixelSize(13)
        p.setFont(font)
        fm = QFontMetrics(font)
        rect = QRect(0, 0, fm.horizontalAdvance(self._status) + 28, fm.height() + 14)
        rect.moveCenter(QPoint(self._region.width() // 2, self._region.height() // 2))
        self._paint_glass(p, rect)
        p.setPen(QColor(theme.ACCENT))
        p.drawText(rect, Qt.AlignCenter, self._status)

    def _paint_hint(self, p: QPainter) -> None:
        """开头几秒告诉用户怎么退出——置顶窗最怕的就是"关不掉"。"""
        font = QFont(theme.FONT_FAMILY)
        font.setPixelSize(11)
        p.setFont(font)
        fm = QFontMetrics(font)
        text = "Esc 关闭 · 工具条可复制 / 看原文"
        rect = self._least_covering_corner(
            fm.horizontalAdvance(text) + 18, fm.height() + 8)
        if rect is None:
            return  # 各角都压着译文：宁可不提示，也不挡内容（工具条一直都在）
        self._paint_glass(p, rect)
        p.setPen(QColor(theme.TEXT_SECONDARY))
        p.drawText(rect, Qt.AlignCenter, text)

    def _least_covering_corner(self, w: int, h: int, margin: int = 6) -> Optional[QRect]:
        """挑一个不压译文的角放提示（选区内坐标）；都被占就返回 None。

        （左上角留给品牌角标，不参与竞争。）
        """
        rw, rh = self._region.width(), self._region.height()
        candidates = [
            QRect(margin, rh - h - margin, w, h),           # 左下
            QRect(rw - w - margin, rh - h - margin, w, h),  # 右下
            QRect(rw - w - margin, margin, w, h),           # 右上
        ]
        for rect in candidates:
            clear = all(
                card is None or not rect.intersects(card[0])
                for card in self._cards
            )
            if clear:
                return rect
        return None

    def _paint_glass(self, p: QPainter, rect: QRect) -> None:
        """状态条/角标托底：近实白底 + 品牌绿描边，深浅背景上都看得清。"""
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), PILL_RADIUS, PILL_RADIUS)
        p.fillPath(path, QColor(255, 255, 255, 246))
        p.setPen(QPen(QColor(107, 165, 63, 210), 1.5))
        p.drawPath(path)

    def _paint_card(self, p: QPainter, rect: QRect, text: str, px: int,
                    bg: QColor, fg: QColor) -> None:
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), CARD_RADIUS, CARD_RADIUS)
        p.fillPath(path, bg)
        font = QFont(theme.FONT_FAMILY)
        font.setPixelSize(px)
        p.setFont(font)
        p.setPen(fg)
        inner = rect.adjusted(CARD_PAD_X, CARD_PAD_Y, -CARD_PAD_X, -CARD_PAD_Y)
        p.drawText(inner, Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignVCenter, text)

    # ---- 交互 ----

    def mousePressEvent(self, event):
        self.close()   # 工具条是子控件收自己的点击；落到这里的都是空白处，点一下就走

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Space):
            self.close()
        else:
            super().keyPressEvent(event)

    def event(self, ev):
        # 切到别的窗口就收走：置顶覆盖层最烦的是"人都走了它还赖着"
        if ev.type() == QEvent.WindowDeactivate and self.isVisible():
            self.close()
            return True
        return super().event(ev)

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)
