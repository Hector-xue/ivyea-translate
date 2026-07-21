"""共用控件。

AutoGrowTextEdit：高度随内容走的文本框。
主窗口各页已经整页可滚（QScrollArea），框子再各滚各的既难浏览，也逼得布局
去按像素分配高度；改成"框随内容长高、页面负责滚动"后，写死高度全部可以去掉。

必须用 QTextEdit 而不是 QPlainTextEdit：后者的 document().size() 不反映换行
后的真实高度（永远约等于一行），据此算高度会让框永远过矮（v0.9.0 弹窗踩过）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap, QTextOption
from PySide6.QtWidgets import QSizePolicy, QTextEdit

MAX_SIZE = 16777215  # Qt 的尺寸上限，等价于"不封顶"


def screen_dpr() -> float:
    """当前主屏缩放比（用于把图标画成高清位图，避免 125%/150% 下发虚）。"""
    from PySide6.QtGui import QGuiApplication

    try:
        screen = QGuiApplication.primaryScreen()
        return float(screen.devicePixelRatio()) if screen else 1.0
    except Exception:
        return 1.0


def pin_icon(color: str, size: int = 16, opacity: float = 1.0) -> QIcon:
    """自绘图钉图标。

    不用 📌 这类 emoji：Windows 10 上 emoji 由 Segoe UI Emoji 以彩色位图渲染，
    字形的实际外框比字号大（还带自己的行距），塞进 26x26 的小按钮里必然被裁掉
    一角——这就是 v0.3.x 图钉"显示不完全"的原因。改成按当前 DPI 画的矢量图标，
    尺寸完全可控，颜色也能跟品牌走。
    """
    dpr = screen_dpr()
    px = max(1, int(round(size * dpr)))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    pm.setDevicePixelRatio(dpr)

    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.scale(px / 24.0, px / 24.0)  # 以下坐标一律在 24x24 的图标格里
    path = QPainterPath()
    path.addRoundedRect(8.0, 2.0, 8.0, 3.2, 1.4, 1.4)        # 钉帽
    body = QPainterPath()
    body.moveTo(9.6, 5.2)
    body.lineTo(14.4, 5.2)
    body.lineTo(16.2, 13.6)
    body.lineTo(7.8, 13.6)
    body.closeSubpath()                                       # 钉身
    path = path.united(body)
    plate = QPainterPath()
    plate.addRoundedRect(6.0, 13.2, 12.0, 2.4, 1.2, 1.2)      # 底盘
    path = path.united(plate)
    needle = QPainterPath()
    needle.moveTo(11.2, 15.4)
    needle.lineTo(12.8, 15.4)
    needle.lineTo(12.0, 21.6)
    needle.closeSubpath()                                     # 针尖
    path = path.united(needle)
    c = QColor(color)  # 只吃 #RRGGBB 这类 QColor 认得的写法，透明度走 opacity
    c.setAlphaF(max(0.0, min(1.0, opacity)))
    p.fillPath(path, c)
    p.end()
    return QIcon(pm)


class AutoGrowTextEdit(QTextEdit):
    """随内容自动长高的文本框。

    max_height=0 表示不封顶（自身永不出现滚动条，交给外层页面滚）；
    给了 max_height 则长到上限后自身出滚动条（弹窗那种固定尺寸容器要用）。
    set_free() 切换为跟随布局自由伸展（用户手动拖过尺寸后不再自动算高）。
    """

    def __init__(self, min_height: int = 60, max_height: int = 0, parent=None):
        super().__init__(parent)
        self._min_h = min_height
        self._max_h = max_height
        self._free = False
        # QTextEdit 默认吃富文本：从网页粘贴会把字体/字号/颜色一起带进来
        # （QPlainTextEdit 时代不存在这个问题），一律按纯文本收
        self.setAcceptRichText(False)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarAsNeeded if max_height else Qt.ScrollBarAlwaysOff
        )
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.textChanged.connect(self._adjust_height)

    def set_free(self) -> None:
        """交给布局管高度（弹窗被手动拉伸后用）。"""
        self._free = True
        self.setMinimumHeight(48)
        self.setMaximumHeight(MAX_SIZE)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._adjust_height()  # 宽度变了要按新换行重算高度

    def showEvent(self, event):
        super().showEvent(event)
        self._adjust_height()  # 首次显示前 viewport 宽度还是 0，量不出高度

    def _adjust_height(self) -> None:
        if self._free:
            return
        width = self.viewport().width()
        if width <= 0:
            return  # 尚未布局，show/resize 后会再来一次
        doc = self.document()
        doc.setTextWidth(width)  # 关键：按实际宽度换行后再量高，否则长文本被低估
        target = int(doc.size().height()) + 2 * self.frameWidth() + 4
        target = max(self._min_h, target)
        if self._max_h:
            target = min(target, self._max_h)
        if self.height() != target:
            self.setFixedHeight(target)
