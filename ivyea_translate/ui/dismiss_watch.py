"""弹窗"点外部/切走即关"的全局信号源。

TranslationPopup 设了 WA_ShowWithoutActivating（划词时不能抢走用户正在打字的
焦点），代价是 Qt 自己的失焦事件一概不可靠——弹窗从来就没"得到过焦点"，何谈
失去。所以"用户已经走了"的信号只能从系统层面拿。

Windows 上曾用 pynput 低级鼠标钩子（v0.26.0），实测是性能灾难：钩子把整机
每一个鼠标事件（连 WM_MOUSEMOVE 都算）拽进 Python 过一遍 GIL，弹窗一开
系统就发肉，流式翻译跟着卡。所以 Windows 改零钩子轮询：QTimer 每 120ms 读
GetAsyncKeyState（&0x8000 当前按下 + &1 自上次调用以来按过，快速点击不漏）
判定点击，读 GetForegroundWindow 判定前台切换（顺带覆盖纯键盘 Alt+Tab）。
代价是 Windows 没有"滚轮即关"（滚轮没法轮询），点击/切窗已覆盖主场景。

非 Windows 仍走 pynput 全局监听，两条铁律：回调跑在 pynput 线程里，只允许
emit（跨线程信号自动 queued），绝不碰 widget；不用回调给的坐标，主线程槽里
自己读 QCursor.pos()，与 frameGeometry() 天然同一坐标系。

监听/轮询只在"存在未钉住弹窗"期间运行。Wayland 等拿不到全局监听的环境
start() 返回 False，行为退回"手动关闭"。
"""
from __future__ import annotations

import logging
import sys
from typing import List, Optional, Sequence, Tuple

from PySide6.QtCore import QObject, QPoint, QRect, QTimer, Signal

log = logging.getLogger(__name__)

_WINDOWS = sys.platform == "win32"
POLL_MS = 120
_VK_BUTTONS = (0x01, 0x02, 0x04)  # 左 / 右 / 中键


def _get_user32():
    import ctypes

    return ctypes.windll.user32


def foreground_window_id() -> int:
    """当前前台窗口句柄；非 Windows / 取不到返回 0。"""
    if not _WINDOWS:
        return 0
    try:
        return int(_get_user32().GetForegroundWindow())
    except Exception:
        return 0


def popups_to_close(pos: QPoint, popups: Sequence[Tuple[QRect, bool]]) -> List[int]:
    """纯函数（可单测）：给定点击点和 (弹窗区域, 是否钉住) 列表，返回该关的下标。"""
    return [
        i for i, (geom, pinned) in enumerate(popups)
        if not pinned and not geom.contains(pos)
    ]


class GlobalDismissWatcher(QObject):
    """全局"用户走了"探测。start/stop 幂等。

    Windows：主线程 QTimer 轮询（无钩子无新线程）；其余平台：pynput 监听线程。
    """

    mouse_pressed = Signal()
    mouse_scrolled = Signal()      # 仅 pynput 路径会发
    foreground_changed = Signal()  # 仅 Windows 轮询路径会发

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._listener = None
        self._timer: Optional[QTimer] = None
        self._user32 = None
        self._btn_down = {vk: False for vk in _VK_BUTTONS}
        self._last_fg = 0

    @property
    def running(self) -> bool:
        return self._listener is not None or (
            self._timer is not None and self._timer.isActive())

    def start(self) -> bool:
        if self.running:
            return True
        if _WINDOWS and self._start_polling():
            log.info("弹窗关闭监听：轮询模式")
            return True
        ok = self._start_pynput()
        log.info("弹窗关闭监听：%s", "pynput 模式" if ok else "不可用（仅手动关闭）")
        return ok

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass

    # ---- Windows：零钩子轮询 ----

    def _start_polling(self) -> bool:
        try:
            self._user32 = _get_user32()
            # 消掉"自上次调用以来按过"的残留位，并记下当前前台窗口做基线。
            # 基线必须取真实按下状态：监听常在一次点击（点"弹窗"按钮催生弹窗）
            # 的按住期间启动，记成 False 会让下个 tick 把这次按住误判成新点击
            for vk in _VK_BUTTONS:
                state = int(self._user32.GetAsyncKeyState(vk)) & 0xFFFF
                self._btn_down[vk] = bool(state & 0x8000)
            self._last_fg = int(self._user32.GetForegroundWindow())
        except Exception as e:
            log.info("鼠标轮询不可用：%s", e)
            return False
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.setInterval(POLL_MS)
            self._timer.timeout.connect(self._poll)
        self._timer.start()
        return True

    def _poll(self) -> None:
        try:
            pressed = False
            for vk in _VK_BUTTONS:
                state = int(self._user32.GetAsyncKeyState(vk)) & 0xFFFF
                down = bool(state & 0x8000)
                if (state & 0x0001) or (down and not self._btn_down[vk]):
                    pressed = True
                self._btn_down[vk] = down
            if pressed:
                self.mouse_pressed.emit()
            fg = int(self._user32.GetForegroundWindow())
            if fg != self._last_fg:
                self._last_fg = fg
                self.foreground_changed.emit()
        except Exception:
            pass

    # ---- 非 Windows：pynput 监听线程 ----

    def _start_pynput(self) -> bool:
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
