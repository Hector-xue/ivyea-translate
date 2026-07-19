"""DeepL 式划词气泡：选中文字松开鼠标后，在光标旁弹出小图标，点击即翻译。

组成：
 - classify_gesture：纯函数（可单测），根据按下/松开的位置时间判定
   是否是"划选"（拖拽距离够大）或"双击选词"手势。
 - SelectionWatcher：Windows 下的轮询线程（GetAsyncKeyState + GetCursorPos，
   无钩子、无注入，20ms 一拍），检测到手势 emit bubble_request(x, y)。
   非 Windows 平台为空实现（开发机不启用）。
 - SelectionBubble：无焦点小圆窗，显示品牌 logo，点击触发翻译；
   几秒不点自动消失。不抢焦点，因此点击时原窗口选区不丢。
"""
from __future__ import annotations

import logging
import math
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QLabel, QVBoxLayout, QWidget

from .ui import theme

log = logging.getLogger(__name__)

_WINDOWS = sys.platform == "win32"

DRAG_MIN_PX = 12          # 拖拽超过该距离视为划选（选短词位移也小）
DBLCLICK_MAX_GAP = 0.5    # 双击最大间隔（秒）
DBLCLICK_MAX_DIST = 8     # 双击两次位置最大偏移
CLICK_MAX_DURATION = 0.6  # 单次点击按住超过该时长且没拖动，不算手势
POLL_INTERVAL = 0.015
BUBBLE_TIMEOUT_MS = 4000  # 气泡自动消失


@dataclass
class ClickInfo:
    down_pos: Tuple[int, int]
    down_time: float
    up_pos: Tuple[int, int]
    up_time: float


def _dist(a: Tuple[int, int], b: Tuple[int, int]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def classify_gesture(current: ClickInfo, previous: Optional[ClickInfo]) -> Optional[str]:
    """判定一次鼠标释放是否可能产生了文本选区。

    返回 "drag"（划选）、"dblclick"（双击选词）或 None。
    """
    if _dist(current.down_pos, current.up_pos) >= DRAG_MIN_PX:
        # 按住太久的大距离拖动也算划选（慢慢划字很常见），不限时长
        return "drag"
    if (
        previous is not None
        and current.up_time - previous.up_time <= DBLCLICK_MAX_GAP
        and _dist(current.up_pos, previous.up_pos) <= DBLCLICK_MAX_DIST
        and current.up_time - current.down_time <= CLICK_MAX_DURATION
    ):
        return "dblclick"
    return None


class SelectionWatcher(QObject):
    """Windows：轮询鼠标左键状态，识别划选/双击手势。"""

    bubble_request = Signal(int, int)  # 松开位置（屏幕逻辑坐标）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._enabled = False
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    @property
    def available(self) -> bool:
        return _WINDOWS

    def set_enabled(self, on: bool) -> None:
        self._enabled = on
        if on and _WINDOWS and (self._thread is None or not self._thread.is_alive()):
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True, name="selection-watch")
            self._thread.start()

    def stop(self) -> None:
        self._enabled = False
        self._stop.set()

    def _loop(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        VK_LBUTTON = 0x01

        def cursor_pos() -> Tuple[int, int]:
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            return pt.x, pt.y

        pressed = False
        down_pos: Tuple[int, int] = (0, 0)
        down_time = 0.0
        prev_click: Optional[ClickInfo] = None
        log.info("划词气泡监听已启动")
        while not self._stop.is_set():
            time.sleep(POLL_INTERVAL)
            if not self._enabled:
                prev_click = None
                pressed = False
                time.sleep(0.2)
                continue
            down = bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
            if down and not pressed:
                pressed = True
                down_pos = cursor_pos()
                down_time = time.monotonic()
            elif not down and pressed:
                pressed = False
                info = ClickInfo(down_pos, down_time, cursor_pos(), time.monotonic())
                gesture = classify_gesture(info, prev_click)
                prev_click = info
                if gesture:
                    log.info("划词手势：%s @ %s", gesture, info.up_pos)
                    self.bubble_request.emit(info.up_pos[0], info.up_pos[1])


class SelectionBubble(QWidget):
    """选中文字后出现的小图标；点击触发翻译。常驻单例，show/hide 复用。"""

    clicked = Signal()

    SIZE = 40

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.Tool
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(self.SIZE + 12, self.SIZE + 12)

        card = QWidget(self)
        card.setObjectName("BubbleCard")
        card.setStyleSheet(
            f"""
            QWidget#BubbleCard {{
                background: white;
                border: 1px solid rgba(107, 165, 63, 0.35);
                border-radius: 10px;
            }}
            """
        )
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(16)
        shadow.setOffset(0, 3)
        shadow.setColor(Qt.GlobalColor.gray)
        card.setGraphicsEffect(shadow)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 2, 8, 10)
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(0, 0, 0, 0)
        icon = QLabel()
        icon.setAlignment(Qt.AlignCenter)
        logo = theme.asset_path("logo.png")
        if logo:
            icon.setPixmap(QPixmap(logo).scaled(
                self.SIZE - 12, self.SIZE - 12, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            icon.setText("译")
            icon.setStyleSheet(f"color: {theme.ACCENT}; font-weight: bold; font-size: 18px;")
        lay.addWidget(icon)
        self.setCursor(Qt.PointingHandCursor)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)
        self._noactivate_done = False

    def _apply_noactivate(self) -> None:
        """Windows：挂 WS_EX_NOACTIVATE|WS_EX_TOOLWINDOW。
        Qt 的 WindowDoesNotAcceptFocus 挡不住鼠标点击时的窗口激活——
        一旦气泡抢了激活，注入的 Ctrl+C 会发给气泡自己导致取词失败。"""
        if self._noactivate_done or not _WINDOWS:
            return
        try:
            import ctypes

            GWL_EXSTYLE = -20
            WS_EX_NOACTIVATE = 0x08000000
            WS_EX_TOOLWINDOW = 0x00000080
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
            self._noactivate_done = True
        except Exception:
            log.exception("设置 WS_EX_NOACTIVATE 失败")

    def pop_at(self, x: int, y: int) -> None:
        """在选区松开位置右下方弹出（x/y 为 Qt 逻辑坐标），屏幕边缘自动内收。"""
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.screenAt(QPoint(x, y)) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        px = min(x + 10, geo.x() + geo.width() - self.width() - 4)
        py = min(y + 12, geo.y() + geo.height() - self.height() - 4)
        self.move(px, py)
        self.show()
        self._apply_noactivate()
        self.raise_()
        self._hide_timer.start(BUBBLE_TIMEOUT_MS)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.hide()
            self.clicked.emit()
            event.accept()
