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

from .clipboard_watch import ClipboardWatcher
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
    """后台线程 -> 主线程的信号桥（截图 OCR 用）。"""

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
        # 恢复上次命中的免费翻译端点，避免本次首译又从 DeepL 慢重试
        from .free_engine import free_engine
        free_engine.preferred = self.cfg.get("free_engine.preferred") or None

        self.bridge = _Bridge()
        self.bridge.ocr_ready.connect(self._on_ocr_ready)
        self.bridge.ocr_failed.connect(self._on_ocr_failed)

        # 划词翻译触发：Ctrl+C+C（文本已在剪贴板，零注入最可靠）
        self.watcher = ClipboardWatcher(max_chars=int(self.cfg.get("double_copy.max_chars", 3000)))
        self.watcher.double_copy_enabled = bool(self.cfg.get("double_copy.enabled", True))
        self.watcher.double_window_s = float(self.cfg.get("double_copy.window_ms", 700)) / 1000
        self.watcher.double_copied.connect(self._popup_translate_at_cursor)

        self.window = MainWindow(self.cfg)
        self.window.settings_saved.connect(self._on_settings_saved)

        # 热键注册放在窗口之后，注册结果直接显示到设置页
        self.hotkeys = HotkeyManager()
        self.hotkeys.screenshot_translate.connect(self.trigger_screenshot_translate)
        self._register_hotkeys()

        self._popups: List[TranslationPopup] = []
        self._workers: List[TranslateWorker] = []
        self._overlay: Optional[CaptureOverlay] = None

        self._setup_tray()
        ocr_engine.warmup_async()

        from PySide6.QtCore import QTimer

        # 首次启动显示上手引导
        if not bool(self.cfg.get("onboarded", False)):
            QTimer.singleShot(600, self._maybe_onboard)
        # 启动 8 秒后后台静默检查更新（失败无感知）
        if bool(self.cfg.get("update.auto_check", True)):
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
        menu.addSeparator()
        # 临时暂停"Ctrl+C+C"监听（大量复制代码时用）
        self.act_pause = QAction("暂停划词翻译", menu)
        self.act_pause.setCheckable(True)
        self.act_pause.setChecked(not self.watcher.double_copy_enabled)
        self.act_pause.toggled.connect(self._toggle_pause)
        menu.addAction(self.act_pause)
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
        self.watcher.max_chars = int(self.cfg.get("double_copy.max_chars", 3000))
        self.watcher.double_copy_enabled = bool(self.cfg.get("double_copy.enabled", True))

    def _toggle_pause(self, paused: bool) -> None:
        """临时暂停/恢复"Ctrl+C+C"监听（仅本次运行，不写配置）。"""
        self.watcher.double_copy_enabled = not paused

    def mark_own_copy(self, text: str) -> None:
        """弹窗/主窗口'复制译文'时调用，防止双击复制被自家写入干扰。"""
        self.watcher.mark_own_copy(text)

    # ---------- 弹窗翻译（Ctrl+C+C 触发） ----------

    def _explain_available(self) -> bool:
        """详解需要大模型；配了 API Key 才显示"详解"按钮。"""
        return bool((self.cfg.get("provider.api_key") or "").strip())

    def _popup_translate_at_cursor(self, text: str) -> None:
        popup = TranslationPopup(original=text, show_original=False,
                                 width=int(self.cfg.get("ui.popup_width", 520)),
                                 show_explain=self._explain_available())
        self._track_popup(popup)
        popup.show_at_cursor()
        self._start_translate(popup, text)

    def _resolve_target(self, text: str, explicit: str = "") -> str:
        """决定本次翻译的目标语言。explicit 非空时优先；"auto" 走智能方向。"""
        setting = explicit or self.cfg.get("translate.target_language", "auto")
        if setting == "auto":
            from .langdetect import choose_target

            return choose_target(
                text,
                self.cfg.get("translate.primary_language", "zh-CN"),
                self.cfg.get("translate.secondary_language", "en"),
            )
        return setting

    def _start_translate(self, popup: TranslationPopup, text: str, target_lang: str = "") -> None:
        from .free_engine import resolve_engine

        try:
            client = resolve_engine(self.cfg)
        except LLMError as e:
            popup.set_failed(str(e))
            return
        target = self._resolve_target(text, target_lang)
        worker = TranslateWorker(
            client, text, target, self.cfg.get("translate.style", "general"),
        )
        self._workers.append(worker)
        worker.chunk.connect(popup.append_chunk)
        worker.finished_ok.connect(
            lambda full, s=text, t=target: self._on_popup_done(popup, s, full, t)
        )
        worker.failed.connect(popup.set_failed)
        worker.finished.connect(lambda w=worker: self._workers.remove(w) if w in self._workers else None)
        popup.destroyed.connect(worker.cancel)
        worker.start()

    def _on_popup_done(self, popup: TranslationPopup, source: str, result: str, target: str) -> None:
        popup.set_done(result)
        self.window.add_history(source, result, target, self.cfg.get("translate.style", "general"))

    def _track_popup(self, popup: TranslationPopup) -> None:
        self._popups.append(popup)
        popup.explain_requested.connect(lambda p=popup: self._on_explain_requested(p))
        popup.destroyed.connect(lambda: self._popups.remove(popup) if popup in self._popups else None)

    def _on_explain_requested(self, popup: TranslationPopup) -> None:
        """弹窗点"详解"：讲解外语侧（用母语书写），流式回填。仅大模型可用。"""
        from .langdetect import is_language
        from .llm import client_from_config
        from .translator import build_explain_messages

        translation = popup.result_view.toPlainText().strip()
        if not translation:
            return
        try:
            client = client_from_config(self.cfg)
        except LLMError:
            popup.set_explain_failed("详解需要配置大模型：设置 → 翻译模型 填写 API Key")
            return
        primary = self.cfg.get("translate.primary_language", "zh-CN")
        source = popup.original_text or ""
        # 讲解"外语"那一侧：源文若非母语则讲源文，否则讲译文
        if source and not is_language(source, primary):
            focus, ref = source, translation
        else:
            focus, ref = translation, source
        popup.set_explain_status("详解生成中…")
        worker = TranslateWorker(
            client, focus, primary, "general", parent=self,
            messages=build_explain_messages(focus, ref, primary),
        )
        self._workers.append(worker)
        worker.chunk.connect(popup.append_explain_chunk)
        worker.finished_ok.connect(lambda full: popup.set_explain_done(full))
        worker.failed.connect(popup.set_explain_failed)
        worker.finished.connect(lambda w=worker: self._workers.remove(w) if w in self._workers else None)
        popup.destroyed.connect(worker.cancel)
        worker.start()

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
                                 width=int(self.cfg.get("ui.popup_width", 520)),
                                 show_explain=self._explain_available())
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

    # ---------- 首次引导 ----------

    def _maybe_onboard(self) -> None:
        if self.cfg.get("onboarded", False):
            return
        self.cfg.set("onboarded", True)
        self.cfg.save()
        from PySide6.QtWidgets import QMessageBox

        box = QMessageBox(self.window)
        box.setWindowTitle("欢迎使用 Ivyea Translate")
        box.setIcon(QMessageBox.Information)
        box.setText(
            "三步上手：\n\n"
            "1. 选中任意文字，按 Ctrl+C+C（连按两下 C）—— 立即翻译\n"
            "2. 按 Ctrl+Alt+S 框选屏幕 —— 截图翻译\n"
            "3. 免配置即用（内置免费翻译）；到「设置」填自己的大模型可解锁风格与邮件助手\n\n"
            "程序常驻托盘，点托盘图标可随时打开本窗口。"
        )
        box.exec()

    # ---------- 更新 ----------

    def _auto_check_update(self) -> None:
        from .updater import UpdateChecker

        self._upd_checker = UpdateChecker(
            self.cfg.get("update.feed_url") or "https://translate.ivyea.com/download/version.json"
        )
        self._upd_checker.update_available.connect(self._on_update_found)
        self._upd_checker.start()

    def _on_update_found(self, feed: dict) -> None:
        self.window.show_update_available(feed)  # 设置页也留一个入口
        # 同一版本只主动弹一次，避免每次启动打扰
        if str(feed.get("version")) == str(self.cfg.get("update.prompted_version", "")):
            return
        self.cfg.set("update.prompted_version", feed.get("version", ""))
        self.cfg.save()
        from PySide6.QtWidgets import QMessageBox

        box = QMessageBox(self.window)
        box.setWindowTitle("发现新版本")
        box.setIcon(QMessageBox.Information)
        notes = (feed.get("notes") or "").strip()
        text = (f"Ivyea Translate v{feed['version']} 可用。\n\n"
                "点「立即更新」将自动下载并安装，完成后自动重启——无需手动去官网下载。")
        if notes:
            text += f"\n\n更新内容：\n{notes[:280]}"
        box.setText(text)
        now_btn = box.addButton("立即更新", QMessageBox.AcceptRole)
        box.addButton("以后", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is now_btn:
            self._start_update(feed)

    def _start_update(self, feed: dict) -> None:
        """一键更新：下载(进度条)→静默安装→自动重启。便携版引导到官网。"""
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        from PySide6.QtWidgets import QMessageBox, QProgressDialog
        from .updater import UpdateDownloader, apply_update_and_quit, is_installed_copy

        if not is_installed_copy():
            QMessageBox.information(
                self.window, "更新",
                "便携版无法自替换，请到官网下载新版覆盖使用。")
            QDesktopServices.openUrl(QUrl(feed.get("page_url", "https://translate.ivyea.com/")))
            return

        dlg = QProgressDialog("正在下载新版本…", "取消", 0, 100, self.window)
        dlg.setWindowTitle("更新 Ivyea Translate")
        dlg.setMinimumWidth(380)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        self._upd_cancelled = False
        dlg.canceled.connect(lambda: setattr(self, "_upd_cancelled", True))

        dl = UpdateDownloader(feed["setup_url"], feed["version"], parent=self)
        self._update_dl = dl
        dl.progress.connect(dlg.setValue)

        def done(path):
            if self._upd_cancelled:
                return
            dlg.setLabelText("下载完成，正在安装并重启…")
            dlg.setValue(100)
            apply_update_and_quit(path, self.request_quit)

        def failed(msg):
            dlg.close()
            if not self._upd_cancelled:
                QMessageBox.warning(self.window, "更新失败", f"{msg}\n可稍后重试，或到官网手动下载。")

        dl.finished_ok.connect(done)
        dl.failed.connect(failed)
        dl.start()
        dlg.show()

    # ---------- 退出 ----------

    def request_quit(self) -> None:
        """唯一正确的退出入口：先放行主窗口的 close，再 quit。"""
        self.window.really_quit = True
        self.hotkeys.stop()
        # 记住本次命中的免费端点，下次启动优先用
        try:
            from .free_engine import free_engine
            if free_engine.preferred:
                self.cfg.set("free_engine.preferred", free_engine.preferred)
                self.cfg.save()
        except Exception:
            pass
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
