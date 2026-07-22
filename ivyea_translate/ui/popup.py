"""翻译结果弹窗：无边框、置顶、可拖动、可调大小、流式刷新、智能定位。

compute_popup_pos 是纯函数（可单测）：给定弹窗尺寸、锚区域（不可覆盖）、屏幕可用区，
返回弹窗左上角坐标。优先锚区正下方，放不下依次尝试上方、右侧、左侧，
都放不下则夹回屏幕内（此时允许重叠，属极端情况）。

截图翻译两段式：先秒出"正在识别文字…"弹窗（set_status），OCR 完成后
set_original 回填原文并开始翻译，消除等待黑箱感。
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple

log = logging.getLogger(__name__)

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QFont, QGuiApplication, QPixmap, QTextCursor, QTextOption
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .widgets import MAX_SIZE, AutoGrowTextEdit, pin_icon, screen_dpr

Rect = Tuple[int, int, int, int]  # x, y, w, h

# 弹窗阴影留边（也是拖拽调整大小的抓取带）
MARGIN_L, MARGIN_T, MARGIN_R, MARGIN_B = 20, 14, 20, 24
# 抓边带内外不对称：卡片外那圈透明留白全给缩放（那里没有任何内容），卡片内
# 只探进极窄一条。否则标题行整条都在"上边缘"判定里，鼠标一放上去就变成上下
# 箭头，可这一行的本职是拖动弹窗，光标必须是普通箭头。
RESIZE_GRAB_OUT = 12      # 卡片边缘往外多少像素算"抓边"
RESIZE_GRAB_IN = 3        # 卡片边缘往内多少像素算"抓边"
MIN_W, MIN_H = 320, 180
FLUSH_MS = 60             # 流式片段的合并刷新间隔
BRAND_NAME = "Ivyea Translate"
BRAND_ICON = 16           # 弹窗品牌小标的边长


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(v, hi))


def compute_popup_pos(popup_w: int, popup_h: int, anchor: Rect, screen: Rect, margin: int = 12) -> Tuple[int, int]:
    ax, ay, aw, ah = anchor
    sx, sy, sw, sh = screen
    # 水平首选：与锚区居中对齐，夹回屏幕
    cx = _clamp(ax + (aw - popup_w) // 2, sx + margin, sx + sw - popup_w - margin)

    below_y = ay + ah + margin
    if below_y + popup_h <= sy + sh - margin:
        return cx, below_y

    above_y = ay - margin - popup_h
    if above_y >= sy + margin:
        return cx, above_y

    cy = _clamp(ay + (ah - popup_h) // 2, sy + margin, sy + sh - popup_h - margin)
    right_x = ax + aw + margin
    if right_x + popup_w <= sx + sw - margin:
        return right_x, cy

    left_x = ax - margin - popup_w
    if left_x >= sx + margin:
        return left_x, cy

    # 四个方向都放不下（锚区几乎占满屏），夹回屏幕内兜底
    return (
        _clamp(ax, sx + margin, max(sx + margin, sx + sw - popup_w - margin)),
        _clamp(below_y, sy + margin, max(sy + margin, sy + sh - popup_h - margin)),
    )


class _AutoGrowText(AutoGrowTextEdit):
    """弹窗里的只读结果区：随内容长高，超过 max_h 自身出滚动条。"""

    def __init__(self, max_h: int = 460, parent=None):
        super().__init__(min_height=60, max_height=max_h, parent=parent)
        self.setReadOnly(True)
        self.setFrameStyle(QFrame.NoFrame)


class TranslationPopup(QWidget):
    """一次翻译一个弹窗实例；关闭即销毁。支持钉住（失焦不关）。"""

    explain_requested = Signal()  # 首次点击"详解"时发出，由 app 接管生成
    pin_toggled = Signal(bool)    # 钉住状态变化；app 据此起停全局"点外即关"监听

    def __init__(self, original: str = "", show_original: bool = False,
                 width: int = 520, show_explain: bool = False):
        super().__init__(None)
        self._show_explain = show_explain
        self._explain_started = False
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self._pinned = False
        self._manual_size = False
        self._drag_offset: Optional[QPoint] = None
        self._resize_edge = ""
        self._resize_start_geom: Optional[QRect] = None
        self._resize_start_global: Optional[QPoint] = None
        self._popup_width = width
        self._pending_result: list = []
        self._pending_explain: list = []
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.timeout.connect(self._on_flush_timeout)
        # 译文/原文区默认高度上限随屏幕自适应，长文本也能一眼浏览
        try:
            avail_h = QGuiApplication.primaryScreen().availableGeometry().height()
        except Exception:
            avail_h = 900
        self._res_max_h = int(min(avail_h * 0.62, 680))
        self._orig_max_h = int(min(avail_h * 0.32, 300))

        card = QWidget(self)
        card.setObjectName("PopupCard")
        card.setStyleSheet(
            f"""
            QWidget#PopupCard {{
                background: {theme.POPUP_BG};
                border: 1px solid {theme.CARD_BORDER};
                border-radius: 16px;
            }}
            QTextEdit {{
                background: transparent;
                border: none;
                padding: 0;
                font-size: 15px;
            }}
            """
        )
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(36)
        shadow.setOffset(0, 6)
        shadow.setColor(Qt.GlobalColor.gray)
        card.setGraphicsEffect(shadow)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 14, 20, 24)  # 给阴影留边
        outer.addWidget(card)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 12, 18, 8)
        lay.setSpacing(8)

        # 标题行：品牌小标 + 状态 + 钉住 + 复制 + 关闭（整行也是拖动把手）
        # 品牌放这里而不是另起一条横幅：标题行本来就有大片留白，塞进去不长高、
        # 不挤译文，而且弹窗常年浮在别人家窗口上，一眼能认出是谁弹的。
        head = QHBoxLayout()
        head.setSpacing(6)
        head.addWidget(self._brand_mark())
        brand = QLabel(BRAND_NAME)
        brand.setObjectName("Wordmark")
        bf = QFont()
        bf.setPointSize(9)
        bf.setBold(True)
        bf.setLetterSpacing(QFont.PercentageSpacing, 102)
        brand.setFont(bf)
        head.addWidget(brand)
        dot = QLabel("·")
        dot.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 13px;")
        head.addWidget(dot)
        # 窄弹窗（ui.popup_width 被调小）优先保状态文字，字标收成只剩 logo
        if width < 460:
            brand.setVisible(False)
            dot.setVisible(False)
        self.status_label = QLabel("翻译中…")
        self.status_label.setObjectName("Hint")
        head.addWidget(self.status_label)
        head.addStretch(1)
        self.explain_btn = QPushButton("详解")
        self.explain_btn.setObjectName("Ghost")
        self.explain_btn.setToolTip("讲解重点词、语法与更地道的说法（需大模型）")
        self.explain_btn.clicked.connect(self._on_explain_clicked)
        self.explain_btn.setVisible(self._show_explain)
        self.pin_btn = QPushButton()
        self.pin_btn.setObjectName("Ghost")
        self.pin_btn.setFixedSize(26, 26)
        self.pin_btn.setIconSize(QSize(15, 15))
        self.pin_btn.setToolTip("钉住（失焦不关闭）")
        self.pin_btn.clicked.connect(self._toggle_pin)
        self._sync_pin_style()
        self.copy_btn = QPushButton("复制")
        self.copy_btn.setObjectName("Ghost")
        self.copy_btn.clicked.connect(self._show_copy_menu)
        close_btn = QPushButton("✕")
        close_btn.setObjectName("Ghost")
        close_btn.setFixedSize(26, 26)
        close_btn.clicked.connect(self.close)
        head.addWidget(self.explain_btn)
        head.addWidget(self.pin_btn)
        head.addWidget(self.copy_btn)
        head.addWidget(close_btn)
        lay.addLayout(head)

        # 原文区（截图翻译展示 OCR 结果，可折叠；可先建后填 set_original）
        self.original_text = original
        self._orig_view: Optional[_AutoGrowText] = None
        self._orig_toggle: Optional[QPushButton] = None
        self._divider: Optional[QFrame] = None
        if show_original:
            self._orig_toggle = QPushButton("原文 ▾")
            self._orig_toggle.setObjectName("Ghost")
            self._orig_toggle.setStyleSheet("text-align: left; font-size: 12px;")
            self._orig_toggle.clicked.connect(self._toggle_original)
            lay.addWidget(self._orig_toggle)
            self._orig_view = _AutoGrowText(max_h=self._orig_max_h)
            self._orig_view.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 13px;")
            lay.addWidget(self._orig_view)
            self._divider = QFrame()
            self._divider.setFrameShape(QFrame.HLine)
            self._divider.setStyleSheet("background: rgba(147,163,136,0.3); max-height: 1px; border: none;")
            lay.addWidget(self._divider)
            if original:
                self._orig_view.setPlainText(original)
            else:
                self._set_original_visible(False)

        # 译文区（流式）
        self.result_view = _AutoGrowText(max_h=self._res_max_h)
        lay.addWidget(self.result_view, 1)

        # 详解区（默认隐藏，点"详解"按需生成）
        self._explain_divider = QFrame()
        self._explain_divider.setFrameShape(QFrame.HLine)
        self._explain_divider.setStyleSheet("background: rgba(147,163,136,0.3); max-height: 1px; border: none;")
        lay.addWidget(self._explain_divider)
        self._explain_label = QLabel("详解")
        self._explain_label.setObjectName("Hint")
        lay.addWidget(self._explain_label)
        self._explain_view = _AutoGrowText(max_h=int(self._res_max_h * 0.8))
        self._explain_view.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 13px;")
        lay.addWidget(self._explain_view)
        for w in (self._explain_divider, self._explain_label, self._explain_view):
            w.setVisible(False)
        self._explain_parts: list = []

        # 拖拽提示（贴右下角，暗示可从边缘缩放）
        tip = QLabel("拖动边缘可调整大小", card)
        tip.setObjectName("Hint")
        tip.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 11px;")
        tip.setAlignment(Qt.AlignRight)
        lay.addWidget(tip)
        self._resize_tip = tip

        self.setFixedWidth(width)
        self._result_parts: list = []
        self._card = card
        self._install_hover_tracking(card)

    def _brand_mark(self) -> QLabel:
        """品牌 logo 小标；资源缺失（未打包/被删）时退回一枚品牌绿圆点。"""
        lb = QLabel()
        path = theme.asset_path("logo.png")
        if path:
            dpr = screen_dpr()
            pm = QPixmap(path)
            if not pm.isNull():
                side = max(1, int(round(BRAND_ICON * dpr)))
                pm = pm.scaled(side, side, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                pm.setDevicePixelRatio(dpr)
                lb.setPixmap(pm)
                lb.setFixedSize(BRAND_ICON, BRAND_ICON)
                return lb
        lb.setText("●")
        lb.setStyleSheet(f"color: {theme.ACCENT}; font-size: 12px;")
        return lb

    # ---- 两段式（截图翻译）：先"识别中"，OCR 完成后回填原文 ----

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)
        self.repaint()

    def set_original(self, text: str) -> None:
        self.original_text = text
        if self._orig_view is not None:
            self._orig_view.setPlainText(text)
            self._set_original_visible(True)
        self._relayout()
        self.repaint()

    def _set_original_visible(self, visible: bool) -> None:
        for w in (self._orig_toggle, self._orig_view, self._divider):
            if w is not None:
                w.setVisible(visible)

    # ---- 流式接口（连接 TranslateWorker 信号） ----

    def _flush_pending(self, view: QTextEdit, buf_attr: str) -> None:
        """把缓冲里的增量一次性追加到文本框末尾。

        关键是"追加"而不是 setPlainText(整篇)：后者每来一个 SSE 片段就让整篇
        文档重排，实测 400 片段要吃掉主线程 3 秒以上，且越往后越慢（前 50 片
        3.5ms/片 → 后 50 片 11.8ms/片）。片段到得比渲染快，主线程事件队列就
        积压，排在所有片段之后的 finished_ok 迟迟轮不到 —— 表现就是"译文早出
        完了，'已翻译'还要等一会儿"。
        """
        pending = getattr(self, buf_attr)
        if not pending:
            return
        setattr(self, buf_attr, [])
        text = "".join(pending)
        cursor = view.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        sb = view.verticalScrollBar()
        sb.setValue(sb.maximum())
        self._relayout()

    def _schedule_flush(self) -> None:
        """合并刷新：60ms 内到达的片段并成一次渲染。"""
        if self._flush_timer.isActive():
            return
        self._flush_timer.start(FLUSH_MS)

    def _on_flush_timeout(self) -> None:
        self._flush_pending(self.result_view, "_pending_result")
        self._flush_pending(self._explain_view, "_pending_explain")
        # Windows 实测：不抢焦点的置顶半透明窗，update() 走的异步重绘偶尔要等
        # 下一次输入事件才上屏（用户反馈"点一下鼠标结果才出来"）。流式刷新和
        # 落定各补一次同步 repaint，把像素立刻推上屏幕。
        self.repaint()

    def append_chunk(self, piece: str) -> None:
        self._result_parts.append(piece)
        self._pending_result.append(piece)
        self._schedule_flush()

    def set_done(self, full_text: str) -> None:
        # 先落定文本再改状态：两者同帧完成，不会出现"译文出完了还写着翻译中"
        log.info("弹窗译文落定（%d 字）", len(full_text))
        self._flush_timer.stop()
        self._pending_result = []
        self.result_view.setPlainText(full_text)
        self._result_parts = [full_text]
        self.status_label.setText("已翻译")
        self._relayout()
        self.repaint()  # 见 _on_flush_timeout：确保结果立刻上屏，不等下次输入事件

    def set_failed(self, message: str) -> None:
        log.info("弹窗翻译失败：%s", message)
        self._flush_timer.stop()
        self._pending_result = []
        self.status_label.setText("失败")
        self.result_view.setPlainText(message)
        self.result_view.setStyleSheet(f"color: {theme.ACCENT};")
        self._relayout()
        self.repaint()

    # ---- 详解模式 ----

    def _on_explain_clicked(self) -> None:
        if not self._explain_started:
            # 首次：请求 app 生成
            if not self.result_view.toPlainText().strip():
                return
            self._explain_started = True
            self._set_explain_visible(True)
            self._explain_view.setPlainText("详解生成中…")
            self.explain_requested.emit()
            self._relayout()
        else:
            # 已有：折叠/展开
            vis = self._explain_view.isVisible()
            self._set_explain_visible(not vis)
            self._relayout()

    def _set_explain_visible(self, visible: bool) -> None:
        for w in (self._explain_divider, self._explain_label, self._explain_view):
            w.setVisible(visible)

    def set_explain_status(self, text: str) -> None:
        self._set_explain_visible(True)
        self._pending_explain = []
        self._explain_view.setPlainText(text)
        self._relayout()

    def append_explain_chunk(self, piece: str) -> None:
        # 首片到达时把"详解生成中…"占位清掉，之后一律追加
        if not self._explain_parts:
            self._explain_view.clear()
        self._explain_parts.append(piece)
        self._pending_explain.append(piece)
        self._schedule_flush()

    def set_explain_done(self, full_text: str) -> None:
        self._pending_explain = []
        self._explain_parts = [full_text]
        self._explain_view.setPlainText(full_text)
        self._relayout()

    def set_explain_failed(self, message: str) -> None:
        self._pending_explain = []
        self._explain_view.setStyleSheet(f"color: {theme.ACCENT}; font-size: 13px;")
        self._explain_view.setPlainText(message)
        self._relayout()

    def _relayout(self) -> None:
        if not self._manual_size:
            self.adjustSize()

    def showEvent(self, event):
        super().showEvent(event)
        # 布局完成后再量一次：此时文本区宽度已确定，能按换行算出正确高度
        QTimer.singleShot(0, self._relayout)

    # ---- 手动调整大小 ----

    def enter_manual_size(self) -> None:
        if self._manual_size:
            return
        self._manual_size = True
        # 解除固定宽/高，允许四向自由拖拽
        self.setMinimumSize(MIN_W, MIN_H)
        self.setMaximumSize(MAX_SIZE, MAX_SIZE)
        # 只放开译文区：原文区继续按内容长高（仍受 _orig_max_h 封顶）。
        # 早期版本把原文区也 set_free()，两个框都成了 Expanding，拉大弹窗时
        # 富余高度被原文区按比例吃掉——原文只有两行也撑成一大片空白、分界线
        # 悬在半空，译文反而被挤在底部小框里。多出来的高度只该给译文。
        self.result_view.set_free()

    # ---- 边缘拖拽缩放（无边框窗口自绘） ----

    def _card_rect(self) -> QRect:
        return QRect(MARGIN_L, MARGIN_T,
                     self.width() - MARGIN_L - MARGIN_R,
                     self.height() - MARGIN_T - MARGIN_B)

    def _edge_at(self, pos: QPoint) -> str:
        r = self._card_rect()
        out, inn = RESIZE_GRAB_OUT, RESIZE_GRAB_IN
        # 需落在卡片纵/横跨度内（含抓取带）才算对应边
        in_x = r.left() - out <= pos.x() <= r.right() + out
        in_y = r.top() - out <= pos.y() <= r.bottom() + out
        left = in_y and r.left() - out <= pos.x() <= r.left() + inn
        right = in_y and r.right() - inn <= pos.x() <= r.right() + out
        top = in_x and r.top() - out <= pos.y() <= r.top() + inn
        bottom = in_x and r.bottom() - inn <= pos.y() <= r.bottom() + out
        return ("top" if top else "") + ("bottom" if bottom else "") + \
               ("left" if left else "") + ("right" if right else "")

    def _install_hover_tracking(self, root: QWidget) -> None:
        """让卡片及其所有子控件把鼠标移动转发上来，光标才能及时归位。

        弹窗自己开了 mouseTracking，但那只覆盖卡片外那圈透明留白；鼠标一旦移进
        卡片，事件落到子控件身上（无按键的 MouseMove 不会自动传给父窗口），
        弹窗再也收不到移动事件，_edge_at 的复位逻辑根本没机会跑；而子控件默认
        继承父窗口光标，于是贴过边之后整张卡片一直卡在上下箭头，直到切窗口才被
        系统重置。这里递归开 tracking + 装事件过滤器，把移动事件补回来。
        """
        for w in [root] + root.findChildren(QWidget):
            w.setMouseTracking(True)
            w.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseMove and isinstance(obj, QWidget):
            if obj is self._card or self._card.isAncestorOf(obj):
                self._sync_hover_cursor(obj.mapTo(self, event.position().toPoint()))
        return super().eventFilter(obj, event)

    def _sync_hover_cursor(self, pos: QPoint) -> None:
        if self._resize_edge or self._drag_offset is not None:
            return  # 拖拽/缩放进行中，光标由按下时那一刻决定
        self.setCursor(self._CURSORS.get(self._edge_at(pos), Qt.ArrowCursor))

    def leaveEvent(self, event):
        if not self._resize_edge and self._drag_offset is None:
            self.unsetCursor()
        super().leaveEvent(event)

    _CURSORS = {
        "left": Qt.SizeHorCursor, "right": Qt.SizeHorCursor,
        "top": Qt.SizeVerCursor, "bottom": Qt.SizeVerCursor,
        "topleft": Qt.SizeFDiagCursor, "bottomright": Qt.SizeFDiagCursor,
        "topright": Qt.SizeBDiagCursor, "bottomleft": Qt.SizeBDiagCursor,
    }

    def _apply_resize(self, gp: QPoint) -> None:
        d = gp - self._resize_start_global
        g = QRect(self._resize_start_geom)
        e = self._resize_edge
        if "left" in e:
            g.setLeft(min(g.left() + d.x(), g.right() - MIN_W))
        if "right" in e:
            g.setRight(max(g.right() + d.x(), g.left() + MIN_W))
        if "top" in e:
            g.setTop(min(g.top() + d.y(), g.bottom() - MIN_H))
        if "bottom" in e:
            g.setBottom(max(g.bottom() + d.y(), g.top() + MIN_H))
        self.setGeometry(g)

    # ---- 定位 ----

    def show_near(self, anchor_rect: QRect) -> None:
        """在锚区域附近弹出（不覆盖锚区域）。锚可以是光标点（w=h=1）或截图框。"""
        self.adjustSize()
        screen = QGuiApplication.screenAt(anchor_rect.center()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x, y = compute_popup_pos(
            self.width(),
            self.height(),
            (anchor_rect.x(), anchor_rect.y(), anchor_rect.width(), anchor_rect.height()),
            (geo.x(), geo.y(), geo.width(), geo.height()),
        )
        self.move(x, y)
        self.show()

    def show_at_cursor(self) -> None:
        pos = QGuiApplication.primaryScreen().availableGeometry().center()
        try:
            from PySide6.QtGui import QCursor

            pos = QCursor.pos()
        except Exception:
            pass
        # 光标下方 = "原文下方"：锚区取光标处一个小点
        self.show_near(QRect(pos.x(), pos.y(), 1, 12))

    # ---- 交互 ----

    @property
    def is_pinned(self) -> bool:
        return self._pinned

    def _toggle_pin(self) -> None:
        self._pinned = not self._pinned
        self._sync_pin_style()
        self.pin_toggled.emit(self._pinned)

    def _sync_pin_style(self) -> None:
        """图钉一律走品牌绿：未钉住淡一档，钉住时实心 + 品牌绿底衬。"""
        self.pin_btn.setIcon(pin_icon(theme.ACCENT, 15, 1.0 if self._pinned else 0.55))
        bg = theme.ACCENT_SOFT if self._pinned else "transparent"
        self.pin_btn.setStyleSheet(
            "QPushButton {"
            f" background: {bg}; border: none; border-radius: 8px; padding: 0;"
            "}"
            f"QPushButton:hover {{ background: {theme.ACCENT_SOFT}; }}"
        )
        self.pin_btn.setToolTip("已钉住（点一下取消）" if self._pinned else "钉住（失焦不关闭）")

    def _toggle_original(self) -> None:
        if self._orig_view is None:
            return
        visible = self._orig_view.isVisible()
        self._orig_view.setVisible(not visible)
        if self._orig_toggle is not None:
            self._orig_toggle.setText("原文 ▸" if visible else "原文 ▾")
        self._relayout()

    def _copy_text(self, text: str) -> None:
        if not text:
            return
        app = QApplication.instance()
        if app and hasattr(app, "mark_own_copy"):
            app.mark_own_copy(text)  # 防止自家写入触发 Ctrl+C+C 划词翻译
        QGuiApplication.clipboard().setText(text)
        self.copy_btn.setText("已复制")
        QTimer.singleShot(1200, self.copy_btn, lambda: self.copy_btn.setText("复制"))

    def _build_copy_menu(self) -> QMenu:
        """构建与弹出拆开：offscreen 测试只验构建，不 exec。"""
        menu = QMenu(self)
        menu.addAction("复制译文", lambda: self._copy_text(self.result_view.toPlainText()))
        act = menu.addAction("复制原文", lambda: self._copy_text(self.original_text))
        act.setEnabled(bool((self.original_text or "").strip()))
        return menu

    def _show_copy_menu(self) -> None:
        menu = self._build_copy_menu()
        menu.exec(self.copy_btn.mapToGlobal(QPoint(0, self.copy_btn.height() + 4)))

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        # 弹窗平时不抢焦点（WA_ShowWithoutActivating）；用户主动点它，才把键盘
        # 焦点接过来——Esc 从这一刻起真正可用
        self.activateWindow()
        self.setFocus(Qt.MouseFocusReason)
        edge = self._edge_at(event.position().toPoint())
        if edge:
            # 从边缘按下 -> 缩放
            self.enter_manual_size()
            self._resize_edge = edge
            self._resize_start_geom = QRect(self.geometry())
            self._resize_start_global = event.globalPosition().toPoint()
        else:
            # 其余区域 -> 移动
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        event.accept()

    def mouseMoveEvent(self, event):
        if self._resize_edge and (event.buttons() & Qt.LeftButton):
            self._apply_resize(event.globalPosition().toPoint())
            event.accept()
            return
        if self._drag_offset is not None and (event.buttons() & Qt.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        # 无按键：靠近边缘时给出缩放光标提示
        self._sync_hover_cursor(event.position().toPoint())

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        self._resize_edge = ""
        self._resize_start_geom = None
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)
