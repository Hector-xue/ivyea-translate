"""共用控件。

AutoGrowTextEdit：高度随内容走的文本框。
主窗口各页已经整页可滚（QScrollArea），框子再各滚各的既难浏览，也逼得布局
去按像素分配高度；改成"框随内容长高、页面负责滚动"后，写死高度全部可以去掉。

必须用 QTextEdit 而不是 QPlainTextEdit：后者的 document().size() 不反映换行
后的真实高度（永远约等于一行），据此算高度会让框永远过矮（v0.9.0 弹窗踩过）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextOption
from PySide6.QtWidgets import QSizePolicy, QTextEdit

MAX_SIZE = 16777215  # Qt 的尺寸上限，等价于"不封顶"


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
