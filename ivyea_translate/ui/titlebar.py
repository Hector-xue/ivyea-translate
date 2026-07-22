"""无边框窗口外壳：自绘标题栏 + 圆角卡片式窗体 + 自绘投影 + 四边缩放。

系统标题栏是一条白色横条，和窗口内的渐变割裂；但只把它去掉又会走到另一个极端
——窗口变成一块直角的色块"贴"在屏幕上，没有边界也没有层次。所以窗体本身要当
一张卡片来画：

    MainWindow（透明）
      └ Root（透明，四周留 SHADOW_MARGIN 作投影与抓边带）
          └ Shell（圆角 + 描边 + 渐变，真正看得见的那扇窗）

投影是自己画的，不用 QGraphicsDropShadowEffect：那个 effect 会把整棵子树先渲染
到离屏 pixmap 再模糊，主窗口这种一直在输入/流式刷新的界面会明显掉帧。这里用一圈
逐层变淡的圆角描边近似高斯投影，纯 2D 绘制，几乎零成本。

拖动/缩放一律走 Qt 的 startSystemMove() / startSystemResize()（交给窗口管理器），
系统级拖动在 Windows 上自带贴边分屏、双击最大化、多屏 DPI 正确缩放，手写版本这些
都做不对。四条边的抓边带就是那圈投影留白：那里没有任何子控件，鼠标事件必定落到
窗口自己身上，所以上下左右四个方向都能拖（v0.22.0 只能左右拖，就是因为上边被标题
栏、下边被内容区吃掉了事件）。

macOS 不走无边框：红绿灯按钮是 Mac 用户的肌肉记忆，去掉反而更别扭，那边保留原生
窗口，标题栏只当头部横幅（不画最小化/最大化/关闭）。
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from . import theme

MACOS = sys.platform == "darwin"
WINDOWS = sys.platform == "win32"

TITLEBAR_HEIGHT = 38
SHADOW_MARGIN = 12   # 窗体四周留给投影的透明留白（同时是抓边缩放带）
SHADOW_OFFSET = 3    # 投影下沉，模拟光从上方来
RESIZE_BAND = SHADOW_MARGIN + 4


def apply_frameless(win) -> bool:
    """把窗口设成无边框 + 透明底（窗体圆角与投影由我们自己画）。

    返回是否生效；macOS 保留原生窗口，返回 False。
    """
    if MACOS:
        return False
    win.setWindowFlags(win.windowFlags() | Qt.FramelessWindowHint)
    win.setAttribute(Qt.WA_TranslucentBackground)
    return True


class TitleBar(QWidget):
    """应用头部 = 标题栏：品牌字标 + 状态 + 窗口按钮，整行可拖动。"""

    def __init__(self, title: str, with_buttons: bool = True, parent=None):
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(TITLEBAR_HEIGHT)
        self._drag_offset: QPoint | None = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 0, 8 if with_buttons else 16, 0)
        lay.setSpacing(9)

        dot = QLabel()
        logo = theme.asset_path("logo.png")
        if logo:
            from PySide6.QtGui import QPixmap

            dot.setPixmap(QPixmap(logo).scaled(
                18, 18, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            dot.setText("●")
            dot.setStyleSheet(f"color: {theme.ACCENT}; font-size: 13px;")
        lay.addWidget(dot)

        # 字标只留品牌名：副标题"随手即译"在标题栏里是噪音，挪到不占位的窗口标题
        name = QLabel(title)
        name.setObjectName("Wordmark")
        f = QFont()
        f.setPointSize(10)
        f.setBold(True)
        f.setLetterSpacing(QFont.PercentageSpacing, 102)
        name.setFont(f)
        lay.addWidget(name)
        lay.addStretch(1)

        self.status = QLabel("")
        self.status.setObjectName("Hint")
        lay.addWidget(self.status)

        self.min_btn = self.max_btn = self.close_btn = None
        if with_buttons:
            lay.addSpacing(2)
            self.min_btn = self._win_button("–", "最小化")            # –
            self.min_btn.clicked.connect(lambda: self.window().showMinimized())
            self.max_btn = self._win_button("□", "最大化")            # □
            self.max_btn.clicked.connect(self.toggle_max_restore)
            self.close_btn = self._win_button("✕", "关闭（隐藏到托盘）")  # ✕
            self.close_btn.setObjectName("WinBtnClose")
            self.close_btn.clicked.connect(lambda: self.window().close())
            for b in (self.min_btn, self.max_btn, self.close_btn):
                lay.addWidget(b)

    def _win_button(self, glyph: str, tip: str) -> QPushButton:
        btn = QPushButton(glyph, self)
        btn.setObjectName("WinBtn")
        btn.setFixedSize(30, 26)
        btn.setToolTip(tip)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setCursor(Qt.ArrowCursor)
        return btn

    # ---- 最大化 / 还原 ----

    def toggle_max_restore(self) -> None:
        win = self.window()
        if win.isMaximized():
            win.showNormal()
        else:
            win.showMaximized()
        self.sync_max_glyph()

    def sync_max_glyph(self) -> None:
        if self.max_btn is None:
            return
        maximized = self.window().isMaximized()
        self.max_btn.setText("❐" if maximized else "□")  # ❐ / □
        self.max_btn.setToolTip("还原" if maximized else "最大化")

    # ---- 拖动窗口 ----

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        win = self.window()
        handle = win.windowHandle()
        if handle is not None and handle.startSystemMove():
            self._drag_offset = None  # 交给窗口管理器（自带贴边分屏）
            return
        # 兜底：手写拖动
        self._drag_offset = event.globalPosition().toPoint() - win.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None

    def mouseDoubleClickEvent(self, event):
        if self.max_btn is not None and event.button() == Qt.LeftButton:
            self.toggle_max_restore()


class ShellWindowMixin:
    """无边框窗口的窗体绘制（圆角投影）与四边缩放。"""

    _frameless: bool = False

    # ---- 投影 ----

    def _shell_margin(self) -> int:
        return 0 if (not self._frameless or self.isMaximized()) else SHADOW_MARGIN

    def paintEvent(self, event):
        margin = self._shell_margin()
        if not margin:
            return super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setBrush(Qt.NoBrush)
        radius = theme.WINDOW_RADIUS
        # 一圈逐层变淡的圆角描边 ≈ 高斯投影：越往外越透明，整体下沉 SHADOW_OFFSET
        for i in range(margin, 0, -1):
            t = i / margin                       # 1 = 最外圈
            alpha = int(theme.SHADOW_ALPHA * (1.0 - t) ** 2.2)
            if alpha <= 0:
                continue
            p.setPen(QPen(QColor(*theme.SHADOW_RGB, alpha), 1))
            rect = QRectF(
                margin - i + 0.5,
                margin - i + SHADOW_OFFSET + 0.5,
                self.width() - 2 * (margin - i) - 1,
                self.height() - 2 * (margin - i) - SHADOW_OFFSET - 1,
            )
            p.drawRoundedRect(rect, radius + i * 0.6, radius + i * 0.6)
        p.end()

    # ---- 四边缩放 ----

    def _edge_at(self, pos) -> Qt.Edges:
        if not self._frameless or self.isMaximized():
            return Qt.Edges()
        x, y, w, h = pos.x(), pos.y(), self.width(), self.height()
        edges = Qt.Edges()
        if x <= RESIZE_BAND:
            edges |= Qt.LeftEdge
        elif x >= w - RESIZE_BAND:
            edges |= Qt.RightEdge
        if y <= RESIZE_BAND:
            edges |= Qt.TopEdge
        elif y >= h - RESIZE_BAND:
            edges |= Qt.BottomEdge
        return edges

    @staticmethod
    def _cursor_for(edges: Qt.Edges):
        if edges in (Qt.LeftEdge | Qt.TopEdge, Qt.RightEdge | Qt.BottomEdge):
            return Qt.SizeFDiagCursor
        if edges in (Qt.RightEdge | Qt.TopEdge, Qt.LeftEdge | Qt.BottomEdge):
            return Qt.SizeBDiagCursor
        if edges in (Qt.LeftEdge, Qt.RightEdge):
            return Qt.SizeHorCursor
        if edges in (Qt.TopEdge, Qt.BottomEdge):
            return Qt.SizeVerCursor
        return Qt.ArrowCursor

    def mousePressEvent(self, event):
        edges = self._edge_at(event.position().toPoint())
        if edges and event.button() == Qt.LeftButton:
            handle = self.windowHandle()
            if handle is not None and handle.startSystemResize(edges):
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._frameless:
            self.setCursor(self._cursor_for(self._edge_at(event.position().toPoint())))
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._frameless:
            self.unsetCursor()
        super().leaveEvent(event)

    # ---- 最大化时收掉投影留白与圆角 ----

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            self._sync_shell_state()

    def _sync_shell_state(self) -> None:
        bar = getattr(self, "titlebar", None)
        if bar is not None:
            bar.sync_max_glyph()
        shell = getattr(self, "shell", None)
        root_lay = getattr(self, "_root_layout", None)
        if shell is None or root_lay is None:
            return
        margin = self._shell_margin()
        root_lay.setContentsMargins(margin, margin, margin, margin)
        backdrop = getattr(self, "backdrop", None)
        if backdrop is not None:
            # 最大化时窗体贴满屏幕、圆角切直角，背景照片的裁剪也要跟着切
            backdrop.set_radius(theme.WINDOW_RADIUS if margin else 0)
        # 最大化时窗体贴满屏幕，圆角会在四角露出桌面 -> 切成直角。
        # 这里用内联样式而不是 QSS 属性选择器（[maximized="true"]）：动态属性在
        # PySide6 里回读不稳，实测选择器不命中，圆角切不掉。
        shell.setStyleSheet(
            "QWidget#Shell { border-radius: 0; border: none; }" if not margin else ""
        )
        self.update()
