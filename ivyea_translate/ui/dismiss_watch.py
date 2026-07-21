"""弹窗"点外部/滚动即关"的全局信号源。

TranslationPopup 设了 WA_ShowWithoutActivating（划词时不能抢走用户正在打字的
焦点），代价是 Qt 自己的失焦事件一概不可靠——弹窗从来就没"得到过焦点"，何谈
失去。所以点击弹窗外部、切窗口、滚动别处这些"用户已经走了"的信号，只能从系统
层面拿：pynput 起一条全局鼠标监听线程，按下/滚轮时发 Qt 信号回主线程。

两条铁律：
- 回调跑在 pynput 线程里，只允许 emit（跨线程信号自动 queued），绝不碰 widget；
- 不用回调给的坐标（Windows 上是物理像素，高分屏下和 Qt 逻辑坐标对不上），
  主线程槽里自己读 QCursor.pos()，与 frameGeometry() 天然同一坐标系。

监听器只在"存在未钉住弹窗"期间运行：不常驻底层钩子，对杀软友好也省资源。
Wayland 等拿不到全局监听的环境 start() 返回 False，行为退回"手动关闭"。
"""
from __future__ import annotations

import logging
from typing import List, Sequence, Tuple

from PySide6.QtCore import QObject, QPoint, QRect, Signal

log = logging.getLogger(__name__)


def popups_to_close(pos: QPoint, popups: Sequence[Tuple[QRect, bool]]) -> List[int]:
    """纯函数（可单测）：给定点击点和 (弹窗区域, 是否钉住) 列表，返回该关的下标。"""
    return [
        i for i, (geom, pinned) in enumerate(popups)
        if not pinned and not geom.contains(pos)
    ]


class GlobalDismissWatcher(QObject):
    """全局鼠标按下/滚轮监听（pynput 线程 -> Qt 信号）。start/stop 幂等。"""

    mouse_pressed = Signal()
    mouse_scrolled = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._listener = None

    @property
    def running(self) -> bool:
        return self._listener is not None

    def start(self) -> bool:
        if self._listener is not None:
            return True
        try:
            from pynput import mouse

            def on_click(x, y, button, pressed):
                try:
                    if pressed:
                        self.mouse_pressed.emit()
                except Exception:
                    pass

            def on_scroll(x, y, dx, dy):
                try:
                    self.mouse_scrolled.emit()
                except Exception:
                    pass

            listener = mouse.Listener(on_click=on_click, on_scroll=on_scroll)
            listener.daemon = True
            listener.start()
        except Exception as e:
            # Wayland/权限受限环境拿不到全局监听：降级为仅手动关闭，不比从前差
            log.info("全局鼠标监听不可用，弹窗退回手动关闭：%s", e)
            return False
        self._listener = listener
        return True

    def stop(self) -> None:
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass
