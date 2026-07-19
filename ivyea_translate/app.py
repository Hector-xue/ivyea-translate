"""应用装配：单实例、系统托盘、热键/剪贴板/截图三条链路接线。"""
from __future__ import annotations

import logging
import sys
import threading
from typing import List, Optional

log = logging.getLogger(__name__)

from PySide6.QtCore import QLockFile, QObject, QRect, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import selection
from .clipboard_watch import ClipboardWatcher
from .selection_bubble import SelectionBubble, SelectionWatcher
from .config import CONFIG_DIR, Config
from .hotkeys import HotkeyManager
from .llm import LLMError
from .ocr import ocr_engine
from .translator import TranslateWorker
from .ui import theme
from .ui.capture_overlay import CaptureOverlay
from .ui.main_window import MainWindow
from .ui.popup import TranslationPopup


def _make_icon() -> QIcon:
    """品牌 logo 图标；资源缺失时退化为程序画的绿色圆点。"""
    logo = theme.asset_path("logo.png")
    if logo:
        return QIcon(logo)
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(theme.ACCENT))
    p.setPen(Qt.NoPen)
    p.drawEllipse(6, 6, 52, 52)
    p.setPen(QColor("white"))
    font = p.font()
    font.setPixelSize(30)
    font.setBold(True)
    p.setFont(font)
    p.drawText(pm.rect(), Qt.AlignCenter, "译")
    p.end()
    return QIcon(pm)


class _Bridge(QObject):
    """后台线程 -> 主线程的信号桥。"""

    selected_text_ready = Signal(str)
    selection_empty = Signal()
    ocr_ready = Signal(str, QRect)   # 识别文本, 锚区域
    ocr_failed = Signal(str, QRect)


class OcrThread(threading.Thread):
    def __init__(self, bridge: _Bridge, image_path: str, anchor: QRect):
        super().__init__(daemon=True)
        self._bridge = bridge
        self._path = image_path
        self._anchor = anchor

    def run(self):
        try:
            text = ocr_engine.recognize(self._path)
            if text.strip():
                self._bridge.ocr_ready.emit(text, self._anchor)
            else:
                self._bridge.ocr_failed.emit("没有识别到文字", self._anchor)
        except Exception as e:
            self._bridge.ocr_failed.emit(str(e), self._anchor)


class TranslateApp(QApplication):
    def __init__(self, argv: List[str]):
        super().__init__(argv)
        self.setQuitOnLastWindowClosed(False)
        self.setStyleSheet(theme.app_qss())
        self.setWindowIcon(_make_icon())

        self.cfg = Config()
        self.bridge = _Bridge()
        self.bridge.selected_text_ready.connect(self._popup_translate_at_cursor)
        self.bridge.selection_empty.connect(self._notify_no_selection)
        self.bridge.ocr_ready.connect(self._on_ocr_ready)
        self.bridge.ocr_failed.connect(self._on_ocr_failed)

        self.watcher = ClipboardWatcher(max_chars=int(self.cfg.get("clipboard_watch.max_chars", 3000)))
        self.watcher.set_enabled(bool(self.cfg.get("clipboard_watch.enabled", False)))
        self.watcher.text_copied.connect(self._popup_translate_at_cursor)
        # 双击 Ctrl+C 触发划词翻译（文本已在剪贴板，零注入最可靠）
        self.watcher.double_copy_enabled = bool(self.cfg.get("double_copy.enabled", True))
        self.watcher.double_window_s = float(self.cfg.get("double_copy.window_ms", 700)) / 1000
        self.watcher.double_copied.connect(self._popup_translate_at_cursor)

        self.window = MainWindow(self.cfg)
        self.window.settings_saved.connect(self._on_settings_saved)

        # 热键注册放在窗口之后，注册结果直接显示到设置页
        self.hotkeys = HotkeyManager()
        self.hotkeys.select_translate.connect(self.trigger_select_translate)
        self.hotkeys.screenshot_translate.connect(self.trigger_screenshot_translate)
        self._register_hotkeys()

        self._popups: List[TranslationPopup] = []
        self._workers: List[TranslateWorker] = []
        self._overlay: Optional[CaptureOverlay] = None

        # 划词气泡（DeepL 式）：划选/双击后光标旁出图标，点击即翻译
        self.bubble = SelectionBubble()
        self.bubble.clicked.connect(self.trigger_select_translate)
        self.sel_watcher = SelectionWatcher()
        self.sel_watcher.bubble_request.connect(self._on_bubble_request)
        self.sel_watcher.set_enabled(
            self.sel_watcher.available and bool(self.cfg.get("selection_bubble.enabled", True))
        )

        self._setup_tray()
        ocr_engine.warmup_async()

        # 启动 8 秒后后台静默检查更新（失败无感知）
        if bool(self.cfg.get("update.auto_check", True)):
            from PySide6.QtCore import QTimer

            QTimer.singleShot(8000, self._auto_check_update)

    # ---------- 装配 ----------

    def _register_hotkeys(self) -> None:
        ok = self.hotkeys.start(self.cfg.get("hotkeys", {}))
        self.window.set_hotkey_status(None if ok else self.hotkeys.last_error)
        if not ok and self.hotkeys.last_error:
            log.warning("热键：%s", self.hotkeys.last_error)

    def _setup_tray(self) -> None:
        self.tray: Optional[QSystemTrayIcon] = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(_make_icon(), self)
        self.tray.setToolTip("Ivyea Translate")
        menu = QMenu()
        act_show = QAction("打开主窗口", menu)
        act_show.triggered.connect(self.show_main_window)
        menu.addAction(act_show)
        act_shot = QAction("截图翻译", menu)
        act_shot.triggered.connect(self.trigger_screenshot_translate)
        menu.addAction(act_shot)
        self.act_watch = QAction("复制翻译", menu)
        self.act_watch.setCheckable(True)
        self.act_watch.setChecked(self.watcher.enabled)
        self.act_watch.toggled.connect(self._toggle_watch)
        menu.addAction(self.act_watch)
        menu.addSeparator()
        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(self.request_quit)
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.show_main_window()
            if reason == QSystemTrayIcon.ActivationReason.Trigger
            else None
        )
        self.tray.show()

    def _on_settings_saved(self) -> None:
        self._register_hotkeys()
        self.watcher.max_chars = int(self.cfg.get("clipboard_watch.max_chars", 3000))
        self.watcher.double_copy_enabled = bool(self.cfg.get("double_copy.enabled", True))
        enabled = bool(self.cfg.get("clipboard_watch.enabled", False))
        self.watcher.set_enabled(enabled)
        if self.tray:
            self.act_watch.setChecked(enabled)
        self.sel_watcher.set_enabled(
            self.sel_watcher.available and bool(self.cfg.get("selection_bubble.enabled", True))
        )

    def _toggle_watch(self, on: bool) -> None:
        self.watcher.set_enabled(on)
        self.cfg.set("clipboard_watch.enabled", on)
        self.cfg.save()

    def mark_own_copy(self, text: str) -> None:
        """弹窗/主窗口'复制译文'时调用，防止复制翻译自触发。"""
        self.watcher.mark_own_copy(text)

    # ---------- 划词翻译 ----------

    def trigger_select_translate(self) -> None:
        def work():
            try:
                text = selection.get_selected_text(pause_watch=self._pause_watch_threadsafe)
            except Exception:
                log.exception("取词流程异常")
                text = None
            if text:
                self.bridge.selected_text_ready.emit(text)
            else:
                self.bridge.selection_empty.emit()

        threading.Thread(target=work, daemon=True).start()

    def _pause_watch_threadsafe(self, paused: bool) -> None:
        # 只改标志位，线程安全足够
        self.watcher._paused = paused

    def _on_bubble_request(self, x: int, y: int) -> None:
        """划词手势 -> 弹小气泡。在自家窗口上划选/截图框选中不弹。

        注意：watcher 给的 x/y 是 Win32 物理像素；Qt 布窗用逻辑像素，
        高 DPI 缩放屏上直接用会偏出很远。改用此刻的 QCursor.pos()（逻辑坐标，
        手势刚松开光标就在选区旁）。"""
        if self._overlay is not None:
            return
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QCursor

        pt = QCursor.pos()
        for w in [self.window, self.bubble, *self._popups]:
            if w is not None and w.isVisible() and w.frameGeometry().contains(pt):
                return
        self.bubble.pop_at(pt.x(), pt.y())

    def _notify_no_selection(self) -> None:
        if self.tray:
            self.tray.showMessage("Ivyea Translate", "没有取到选中文字", QSystemTrayIcon.Information, 1500)

    # ---------- 弹窗翻译（划词 / 复制共用） ----------

    def _popup_translate_at_cursor(self, text: str) -> None:
        popup = TranslationPopup(original=text, show_original=False,
                                 width=int(self.cfg.get("ui.popup_width", 520)))
        self._track_popup(popup)
        popup.show_at_cursor()
        self._start_translate(popup, text)

    def _start_translate(self, popup: TranslationPopup, text: str, target_lang: str = "") -> None:
        from .free_engine import resolve_engine

        try:
            client = resolve_engine(self.cfg)
        except LLMError as e:
            popup.set_failed(str(e))
            return
        worker = TranslateWorker(
            client,
            text,
            target_lang or self.cfg.get("translate.target_language", "zh-CN"),
            self.cfg.get("translate.style", "general"),
        )
        self._workers.append(worker)
        worker.chunk.connect(popup.append_chunk)
        worker.finished_ok.connect(
            lambda full, s=text: self._on_popup_done(popup, s, full)
        )
        worker.failed.connect(popup.set_failed)
        worker.finished.connect(lambda w=worker: self._workers.remove(w) if w in self._workers else None)
        popup.destroyed.connect(worker.cancel)
        worker.start()

    def _on_popup_done(self, popup: TranslationPopup, source: str, result: str) -> None:
        popup.set_done(result)
        self.window.add_history(
            source,
            result,
            self.cfg.get("translate.target_language", "zh-CN"),
            self.cfg.get("translate.style", "general"),
        )

    def _track_popup(self, popup: TranslationPopup) -> None:
        self._popups.append(popup)
        popup.destroyed.connect(lambda: self._popups.remove(popup) if popup in self._popups else None)

    # ---------- 截图翻译 ----------

    def trigger_screenshot_translate(self) -> None:
        if self._overlay is not None:
            return
        self._overlay = CaptureOverlay()
        self._overlay.region_selected.connect(self._on_region_selected)
        self._overlay.cancelled.connect(self._clear_overlay)
        self._overlay.start()

    def _clear_overlay(self) -> None:
        self._overlay = None

    def _on_region_selected(self, rect: QRect, pixmap: QPixmap) -> None:
        self._clear_overlay()
        # 弹窗立即出现（"识别中"状态），OCR 在后台跑完再回填——消除框选后的静默等待
        popup = TranslationPopup(original="", show_original=True,
                                 width=int(self.cfg.get("ui.popup_width", 520)))
        popup.set_status("正在识别文字…")
        self._track_popup(popup)
        popup.show_near(rect)
        self._shot_popup = popup
        popup.destroyed.connect(lambda: setattr(self, "_shot_popup", None))

        import tempfile

        tmp = tempfile.NamedTemporaryFile(
            suffix=".png", prefix="ivyea_shot_", delete=False, dir=str(CONFIG_DIR)
        )
        tmp.close()
        pixmap.save(tmp.name, "PNG")
        OcrThread(self.bridge, tmp.name, rect).start()

    def _on_ocr_ready(self, text: str, anchor: QRect) -> None:
        popup = getattr(self, "_shot_popup", None)
        if popup is None:  # 用户已把"识别中"弹窗关了，不再打扰
            return
        popup.set_original(text)
        popup.set_status("翻译中…")
        # 截图翻译可独立设定目标语言（空 = 跟随全局）
        self._start_translate(popup, text, self.cfg.get("screenshot.target_language", ""))

    def _on_ocr_failed(self, message: str, anchor: QRect) -> None:
        popup = getattr(self, "_shot_popup", None)
        if popup is None:
            return
        popup.set_failed(f"识别失败：{message}")

    # ---------- 更新 ----------

    def _auto_check_update(self) -> None:
        from .updater import UpdateChecker

        self._upd_checker = UpdateChecker(
            self.cfg.get("update.feed_url") or "https://translate.ivyea.com/download/version.json"
        )
        self._upd_checker.update_available.connect(self._on_update_found)
        self._upd_checker.start()

    def _on_update_found(self, feed: dict) -> None:
        self.window.show_update_available(feed)
        if self.tray:
            self.tray.showMessage(
                "Ivyea Translate",
                f"发现新版本 v{feed['version']}，可在设置页一键更新",
                QSystemTrayIcon.Information,
                4000,
            )

    # ---------- 退出 ----------

    def request_quit(self) -> None:
        """唯一正确的退出入口：先放行主窗口的 close，再 quit。"""
        self.window.really_quit = True
        self.hotkeys.stop()
        self.sel_watcher.stop()
        self.quit()

    # ---------- 主窗口 ----------

    def show_main_window(self) -> None:
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()


def _setup_logging() -> None:
    """打包版无控制台，日志落 ~/.ivyea-translate/app.log 供排查。"""
    try:
        logging.basicConfig(
            filename=str(CONFIG_DIR / "app.log"),
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            encoding="utf-8",
        )
    except OSError:
        logging.basicConfig(level=logging.INFO)

    def excepthook(exc_type, exc, tb):
        logging.getLogger("uncaught").error("未捕获异常", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = excepthook


def main() -> int:
    # 单实例锁：第二个实例直接退出
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _setup_logging()
    from . import __version__

    log.info("Ivyea Translate v%s 启动", __version__)
    lock = QLockFile(str(CONFIG_DIR / "app.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        print("Ivyea Translate 已在运行", file=sys.stderr)
        return 0
    app = TranslateApp(sys.argv)
    app.show_main_window()
    code = app.exec()
    lock.unlock()
    return code
