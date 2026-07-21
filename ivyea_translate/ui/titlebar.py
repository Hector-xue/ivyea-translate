"""自绘标题栏 + 无边框窗口支持。

系统标题栏是一条白色横条，和窗口内的浅绿渐变割裂，而且下面还要再画一行应用
标题，同一件事占两行。这里把窗口改成无边框、自己画标题栏，整扇窗从上到下只有
一层渐变。

拖动/缩放一律走 Qt 的 startSystemMove() / startSystemResize()（交给窗口管理器），
而不是像 popup.py 那样自己算 mouseMove：系统级拖动在 Windows 上自带贴边分屏
（Aero Snap）、双击标题栏最大化、多屏 DPI 切换，这些手写版本都做不对。
两个 API 万一在某个平台返回 False，再退回手写拖动兜底。

macOS 不走无边框：红绿灯按钮是 Mac 用户的肌肉记忆，去掉反而更别扭，那边保留
原生窗口，标题栏只当头部横幅（不画最小化/最大化/关闭）。
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from . import theme

MACOS = sys.platform == "darwin"
WINDOWS = sys.platform == "win32"

TITLEBAR_HEIGHT = 40
RESIZE_BAND = 8  # 距窗口边缘多少像素内算"抓边缩放"


def apply_frameless(win) -> bool:
    """把窗口设成无边框；返回是否真的生效（macOS 上不改，返回 False）。"""
    if MACOS:
        return False
    win.setWindowFlags(win.windowFlags() | Qt.FramelessWindowHint)
    return True


def polish_windows_frame(win) -> None:
    """Windows 无边框窗口找回圆角与系统投影（Win11 有效，其余静默降级）。"""
    if not WINDOWS:
        return
    try:
        import ctypes
        from ctypes import wintypes

        hwnd = int(win.winId())
        dwm = ctypes.windll.dwmapi
        # DWMWA_WINDOW_CORNER_PREFERENCE = 33, DWMWCP_ROUND = 2（Win11 圆角）
        pref = ctypes.c_int(2)
        dwm.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), ctypes.c_int(33), ctypes.byref(pref), ctypes.sizeof(pref)
        )

        # 把 1px 的窗框"延伸"进客户区 -> 系统给无边框窗口重新画上投影
        class MARGINS(ctypes.Structure):
            _fields_ = [("cxLeftWidth", ctypes.c_int), ("cxRightWidth", ctypes.c_int),
                        ("cyTopHeight", ctypes.c_int), ("cyBottomHeight", ctypes.c_int)]

        margins = MARGINS(1, 1, 1, 1)
        dwm.DwmExtendFrameIntoClientArea(wintypes.HWND(hwnd), ctypes.byref(margins))
    except Exception:  # noqa: BLE001 —— 纯装饰，任何失败都不该影响窗口可用
        pass


class TitleBar(QWidget):
    """应用头部 = 标题栏：logo + 标题 + 状态 + 窗口按钮，整行可拖动。"""

    def __init__(self, title: str, with_buttons: bool = True, parent=None):
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(TITLEBAR_HEIGHT)
        self._drag_offset: QPoint | None = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 6 if with_buttons else 14, 0)
        lay.setSpacing(8)

        dot = QLabel()
        logo = theme.asset_path("logo.png")
        if logo:
            from PySide6.QtGui import QPixmap

            dot.setPixmap(QPixmap(logo).scaled(
                20, 20, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            dot.setText("●")
            dot.setStyleSheet(f"color: {theme.ACCENT}; font-size: 14px;")
        lay.addWidget(dot)

        label = QLabel(title)
        f = QFont()
        f.setPointSize(12)
        f.setBold(True)
        label.setFont(f)
        lay.addWidget(label)
        lay.addStretch(1)

        self.status = QLabel("")
        self.status.setObjectName("Hint")
        lay.addWidget(self.status)

        self.min_btn = self.max_btn = self.close_btn = None
        if with_buttons:
            lay.addSpacing(4)
            self.min_btn = self._win_button("─", "最小化")       # ─
            self.min_btn.clicked.connect(lambda: self.window().showMinimized())
            self.max_btn = self._win_button("□", "最大化")       # □
            self.max_btn.clicked.connect(self.toggle_max_restore)
            self.close_btn = self._win_button("✕", "关闭（隐藏到托盘）")  # ✕
            self.close_btn.setObjectName("WinBtnClose")
            self.close_btn.clicked.connect(lambda: self.window().close())
            for b in (self.min_btn, self.max_btn, self.close_btn):
                lay.addWidget(b)

    def _win_button(self, glyph: str, tip: str) -> QPushButton:
        btn = QPushButton(glyph, self)
        btn.setObjectName("WinBtn")
        btn.setFixedSize(38, 28)
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


class FramelessResizeMixin:
    """无边框窗口的边缘缩放：贴边 8px 内按下即交给窗口管理器缩放。

    背景区域（Root 那层）不消费鼠标事件，会冒泡到窗口本身，所以这里只在
    窗口级别处理即可；卡片/输入框上的点击不受影响。
    """

    _frameless: bool = False

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

    def changeEvent(self, event):
        super().changeEvent(event)
        from PySide6.QtCore import QEvent

        if event.type() == QEvent.WindowStateChange:
            bar = getattr(self, "titlebar", None)
            if bar is not None:
                bar.sync_max_glyph()
