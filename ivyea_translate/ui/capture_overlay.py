"""截图框选层：冻结当前屏幕画面 -> 全屏遮罩上拖拽框选 -> 发出选区。

物理像素与逻辑坐标的换算集中在这里：
 - QScreen.grabWindow(0) 返回物理像素 pixmap（devicePixelRatio 已设置）
 - 框选用逻辑坐标（Qt 事件坐标），裁剪时乘 dpr 换成物理像素
发出的 region_selected(QRect 逻辑全局坐标, 裁剪图物理像素) 供定位弹窗和 OCR 用。
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from . import theme

MIN_SELECT_SIZE = 8  # 逻辑像素，小于视为误触


class CaptureOverlay(QWidget):
    """一次截图一个实例；Esc/右键取消，松开左键完成。"""

    region_selected = Signal(QRect, QPixmap)  # 全局逻辑坐标选区, 物理像素裁剪图
    cancelled = Signal()

    def __init__(self):
        super().__init__(None)
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        self._screen = screen
        self._screen_geo = screen.geometry()  # 逻辑全局坐标
        self._shot = screen.grabWindow(0)     # 物理像素，dpr 已带
        self._dpr = self._shot.devicePixelRatio() or 1.0

        self._origin: QPoint | None = None
        self._current: QPoint | None = None

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setCursor(Qt.CrossCursor)
        self.setGeometry(self._screen_geo)

    def start(self) -> None:
        self.showFullScreen()
        self.activateWindow()
        self.raise_()

    # ---- 绘制：冻结截图 + 半透明遮罩 + 选框高亮 ----

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(self.rect(), self._shot)
        painter.fillRect(self.rect(), QColor(20, 18, 34, 110))
        sel = self._selection_rect()
        if sel is not None:
            src = QRect(
                int(sel.x() * self._dpr),
                int(sel.y() * self._dpr),
                int(sel.width() * self._dpr),
                int(sel.height() * self._dpr),
            )
            painter.drawPixmap(sel, self._shot, src)  # 选区内显示原亮度
            pen = QPen(QColor(theme.ACCENT), 2)
            painter.setPen(pen)
            painter.drawRoundedRect(sel, 4, 4)
            painter.setPen(QColor(255, 255, 255, 220))
            label = f"{sel.width()} × {sel.height()}"
            painter.drawText(sel.x(), max(16, sel.y() - 8), label)
        else:
            painter.setPen(QColor(255, 255, 255, 230))
            painter.drawText(
                self.rect().adjusted(0, 24, 0, 0),
                Qt.AlignHCenter | Qt.AlignTop,
                "拖拽框选要翻译的区域 · Esc 取消",
            )

    def _selection_rect(self) -> QRect | None:
        """窗口内逻辑坐标选区。"""
        if self._origin is None or self._current is None:
            return None
        return QRect(self._origin, self._current).normalized()

    # ---- 交互 ----

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._origin = event.position().toPoint()
            self._current = self._origin
            self.update()
        elif event.button() == Qt.RightButton:
            self._cancel()

    def mouseMoveEvent(self, event):
        if self._origin is not None:
            self._current = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or self._origin is None:
            return
        sel = self._selection_rect()
        self._origin = self._current = None
        if sel is None or sel.width() < MIN_SELECT_SIZE or sel.height() < MIN_SELECT_SIZE:
            self._cancel()
            return
        src = QRect(
            int(sel.x() * self._dpr),
            int(sel.y() * self._dpr),
            int(sel.width() * self._dpr),
            int(sel.height() * self._dpr),
        )
        cropped = self._shot.copy(src)
        cropped.setDevicePixelRatio(1.0)  # 交给 OCR 的是纯物理像素图
        global_rect = QRect(sel.translated(self._screen_geo.topLeft()))
        self.close()
        self.region_selected.emit(global_rect, cropped)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._cancel()

    def _cancel(self):
        self.close()
        self.cancelled.emit()
