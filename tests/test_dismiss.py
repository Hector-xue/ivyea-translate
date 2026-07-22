"""弹窗"点外部/滚动即关"：判定纯函数 + 全局监听器的起停与降级。"""
import pytest

from PySide6.QtCore import QPoint, QRect


# ---------- 关闭判定（纯函数） ----------

@pytest.mark.parametrize("pos, popups, expect", [
    # 点在弹窗外 -> 关
    (QPoint(500, 500), [(QRect(0, 0, 300, 200), False)], [0]),
    # 点在弹窗内（含阴影留边的 frameGeometry） -> 不关
    (QPoint(100, 100), [(QRect(0, 0, 300, 200), False)], []),
    # 钉住的弹窗点哪都不关
    (QPoint(500, 500), [(QRect(0, 0, 300, 200), True)], []),
    # 多弹窗各自判定：点中 A，A 留着、B 关掉
    (QPoint(100, 100), [(QRect(0, 0, 300, 200), False),
                        (QRect(400, 400, 300, 200), False)], [1]),
])
def test_popups_to_close(pos, popups, expect):
    from ivyea_translate.ui.dismiss_watch import popups_to_close

    assert popups_to_close(pos, popups) == expect


# ---------- 监听器起停 / 降级 ----------

class _FakeListener:
    instances = []

    def __init__(self, on_click=None, on_scroll=None):
        self.on_click = on_click
        self.on_scroll = on_scroll
        self.started = False
        self.stopped = False
        _FakeListener.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


def _install_fake_pynput(monkeypatch, listener_cls):
    """往 sys.modules 塞假 pynput：无头 CI 上 import pynput.mouse 会因缺 Xlib
    直接 ImportError，真模块根本 monkeypatch 不到。"""
    import sys
    import types

    mouse_mod = types.ModuleType("pynput.mouse")
    mouse_mod.Listener = listener_cls
    pkg = types.ModuleType("pynput")
    pkg.mouse = mouse_mod
    monkeypatch.setitem(sys.modules, "pynput", pkg)
    monkeypatch.setitem(sys.modules, "pynput.mouse", mouse_mod)


@pytest.fixture()
def fake_pynput(qapp, monkeypatch):
    _FakeListener.instances = []
    _install_fake_pynput(monkeypatch, _FakeListener)
    yield _FakeListener


def test_watcher_start_stop_idempotent(fake_pynput):
    from ivyea_translate.ui.dismiss_watch import GlobalDismissWatcher

    w = GlobalDismissWatcher()
    assert not w.running
    assert w.start() and w.running
    assert w.start()                       # 再 start 不新建监听
    assert len(fake_pynput.instances) == 1
    w.stop()
    assert not w.running
    assert fake_pynput.instances[0].stopped
    w.stop()                               # 再 stop 不炸


def test_watcher_relays_press_and_scroll_as_signals(fake_pynput):
    """pynput 回调 -> Qt 信号：按下只认 pressed=True，滚轮一律转发。"""
    from ivyea_translate.ui.dismiss_watch import GlobalDismissWatcher

    w = GlobalDismissWatcher()
    w.start()
    listener = fake_pynput.instances[0]
    pressed, scrolled = [], []
    w.mouse_pressed.connect(lambda: pressed.append(1))
    w.mouse_scrolled.connect(lambda: scrolled.append(1))
    listener.on_click(10, 10, None, True)
    listener.on_click(10, 10, None, False)   # 松开不算
    listener.on_scroll(10, 10, 0, -1)
    assert len(pressed) == 1
    assert len(scrolled) == 1
    w.stop()


# ---------- Windows 轮询路径（零钩子；假 user32 驱动） ----------

class _FakeUser32:
    def __init__(self):
        self.states = {}
        self.fg = 111

    def GetAsyncKeyState(self, vk):
        state = self.states.get(vk, 0)
        self.states[vk] = state & 0x8000  # 低位"按过"读一次即清，模拟真实语义
        return state

    def GetForegroundWindow(self):
        return self.fg


@pytest.fixture()
def polling_watcher(qapp, monkeypatch):
    from ivyea_translate.ui import dismiss_watch

    fake = _FakeUser32()
    monkeypatch.setattr(dismiss_watch, "_WINDOWS", True)
    monkeypatch.setattr(dismiss_watch, "_get_user32", lambda: fake)
    w = dismiss_watch.GlobalDismissWatcher()
    assert w.start() and w.running
    yield w, fake
    w.stop()


def test_polling_detects_held_click(polling_watcher):
    w, fake = polling_watcher
    pressed = []
    w.mouse_pressed.connect(lambda: pressed.append(1))
    fake.states[0x01] = 0x8000     # 按下并保持
    w._poll()
    w._poll()                      # 持续按住不重复报
    assert len(pressed) == 1


def test_polling_catches_fast_click_between_ticks(polling_watcher):
    """两次轮询之间完成的快速点击靠 GetAsyncKeyState 的低位补漏。"""
    w, fake = polling_watcher
    pressed = []
    w.mouse_pressed.connect(lambda: pressed.append(1))
    fake.states[0x01] = 0x0001     # 已松开，但"自上次调用以来按过"
    w._poll()
    assert len(pressed) == 1
    w._poll()                      # 低位已被读取清掉，不重复报
    assert len(pressed) == 1


def test_polling_reports_foreground_change(polling_watcher):
    """前台窗口一换就发信号——覆盖纯键盘 Alt+Tab 切走的场景。"""
    w, fake = polling_watcher
    seen = []
    w.foreground_changed.connect(lambda: seen.append(1))
    w._poll()
    assert not seen                # 前台没变不吵
    fake.fg = 222
    w._poll()
    assert len(seen) == 1


def test_watcher_degrades_when_listener_unavailable(qapp, monkeypatch):
    """Wayland 等环境起不了全局监听：start 返回 False，行为退回手动关闭。"""

    def boom(**kwargs):
        raise RuntimeError("no display")

    _install_fake_pynput(monkeypatch, boom)
    from ivyea_translate.ui.dismiss_watch import GlobalDismissWatcher

    w = GlobalDismissWatcher()
    assert w.start() is False
    assert not w.running
