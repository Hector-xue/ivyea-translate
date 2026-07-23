"""应用装配：单实例、系统托盘、热键/剪贴板/截图三条链路接线。"""
from __future__ import annotations

import logging
import sys
import threading
import time
from typing import List, Optional

log = logging.getLogger(__name__)

from PySide6.QtCore import QLockFile, QObject, QRect, Qt, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .clipboard_watch import ClipboardWatcher
from .config import CONFIG_DIR, Config
from .hotkeys import HotkeyManager
from .llm import LLMError
from .ocr import ocr_engine
from .translator import TranslateWorker
from .ui import theme
from .ui.capture_overlay import CaptureOverlay
from .ui.dismiss_watch import GlobalDismissWatcher
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

    ocr_ready = Signal(str, QRect)      # 识别文本, 锚区域（弹窗模式）
    ocr_failed = Signal(str, QRect)
    blocks_ready = Signal(list, QRect)  # 带包围框的段落列表（原位模式）
    blocks_failed = Signal(str, QRect)


class OcrThread(threading.Thread):
    """后台识别。mode="popup" 只要文本；mode="inplace" 还要每段的包围框。

    收 QImage 不收文件路径：截图内存直通 OCR，省掉 PNG 编码落盘再读回解码
    的 50-200ms（QImage 可安全跨线程，QPixmap 不行——转换必须在主线程做完）。
    """

    def __init__(self, bridge: _Bridge, image, anchor: QRect,
                 mode: str = "popup"):
        super().__init__(daemon=True)
        self._bridge = bridge
        self._image = image
        self._anchor = anchor
        self._mode = mode

    def run(self):
        from .ocr import qimage_to_rgb

        inplace = self._mode == "inplace"
        try:
            blocks = ocr_engine.recognize_blocks_array(qimage_to_rgb(self._image))
            if inplace:
                if blocks:
                    self._bridge.blocks_ready.emit(list(blocks), self._anchor)
                else:
                    self._bridge.blocks_failed.emit("没有识别到文字", self._anchor)
                return
            text = "\n\n".join(b.text for b in blocks)
            if text.strip():
                self._bridge.ocr_ready.emit(text, self._anchor)
            else:
                self._bridge.ocr_failed.emit("没有识别到文字", self._anchor)
        except Exception as e:
            sig = self._bridge.blocks_failed if inplace else self._bridge.ocr_failed
            sig.emit(str(e), self._anchor)


class TranslateApp(QApplication):
    def __init__(self, argv: List[str]):
        super().__init__(argv)
        self.setQuitOnLastWindowClosed(False)
        # 配置要先读：全局 QSS 取决于用户选的主题
        self.cfg = Config()
        theme.set_card_opacity(self.cfg.get("ui.card_opacity", None))
        theme.apply(self.cfg.get("ui.theme", theme.DEFAULT_THEME))
        self.setStyleSheet(theme.app_qss())
        self.setWindowIcon(_make_icon())

        # 恢复上次命中的免费翻译端点，避免本次首译又从 DeepL 慢重试
        from .free_engine import free_engine
        free_engine.preferred = self.cfg.get("free_engine.preferred") or None

        self.bridge = _Bridge()
        self.bridge.ocr_ready.connect(self._on_ocr_ready)
        self.bridge.ocr_failed.connect(self._on_ocr_failed)
        self.bridge.blocks_ready.connect(self._on_blocks_ready)
        self.bridge.blocks_failed.connect(self._on_blocks_failed)

        # 划词翻译触发：Ctrl+C+C（文本已在剪贴板，零注入最可靠）
        self.watcher = ClipboardWatcher(max_chars=int(self.cfg.get("double_copy.max_chars", 3000)))
        self.watcher.double_copy_enabled = bool(self.cfg.get("double_copy.enabled", True))
        self.watcher.double_window_s = float(self.cfg.get("double_copy.window_ms", 700)) / 1000
        self.watcher.double_copied.connect(self._popup_translate_at_cursor)

        # 弹窗"点外部/切走即关"：全局探测，只在有未钉住弹窗时运行
        self.dismiss = GlobalDismissWatcher(self)
        self.dismiss.mouse_pressed.connect(self._on_global_press)
        self.dismiss.mouse_scrolled.connect(self._on_global_press)
        self.dismiss.foreground_changed.connect(self._on_foreground_changed)
        self.aboutToQuit.connect(self.dismiss.stop)

        self.window = MainWindow(self.cfg)
        self.window.settings_saved.connect(self._on_settings_saved)

        # 热键注册放在窗口之后，注册结果直接显示到设置页
        self.hotkeys = HotkeyManager()
        self.hotkeys.screenshot_translate.connect(self.trigger_screenshot_translate)
        self.hotkeys.screenshot_inplace.connect(self.trigger_screenshot_inplace)
        self._register_hotkeys()

        self._popups: List[TranslationPopup] = []
        self._workers: List[TranslateWorker] = []
        self._overlay: Optional[CaptureOverlay] = None
        self._capture_mode = "popup"
        self._inplace: Optional[object] = None
        self._ocr_threads: List[OcrThread] = []

        # 退出前必须收干净后台翻译线程：QThread 对象被 Python 回收时若线程还在跑，
        # Qt 会直接 abort（"QThread: Destroyed while thread is still running"）。
        # 正在流式翻译时点退出就会踩到，Windows 上可能弹一次崩溃报告。
        self.aboutToQuit.connect(self._shutdown_workers)

        self._setup_tray()
        ocr_engine.warmup_async()
        self._prewarm_engines()

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
        act_shot = QAction("截图翻译（弹窗）", menu)
        act_shot.triggered.connect(self.trigger_screenshot_translate)
        menu.addAction(act_shot)
        act_inplace = QAction("截图翻译（原位）", menu)
        act_inplace.triggered.connect(self.trigger_screenshot_inplace)
        menu.addAction(act_inplace)
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
        # 接口地址可能变了：作废旧连接池，对新端点重建并预热
        from .llm import reset_http_pool

        reset_http_pool()
        self._prewarm_engines()

    def _prewarm_engines(self) -> None:
        """预热翻译端点的 TLS 连接：首次翻译不付握手税（不发正式请求、不耗 token）。"""
        from .free_engine import prewarm_async as free_prewarm
        from .llm import prewarm_async as llm_prewarm

        if (self.cfg.get("provider.api_key") or "").strip():
            llm_prewarm((self.cfg.get("provider.base_url") or "").strip())
        free_prewarm()

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

    # 刚出生的弹窗豁免"点外即关/切窗即关"：催生它的那次点击、以及它顶掉的
    # 窗口（截图框选层/原位覆盖层）关闭引发的前台交接，都发生在出生后几百毫秒
    # 内——没有豁免期，"弹窗"按钮点出来的对照弹窗会被当场误杀
    POPUP_BIRTH_GRACE_S = 0.8

    def _track_popup(self, popup: TranslationPopup) -> None:
        popup._born_at = time.monotonic()
        self._popups.append(popup)
        popup.explain_requested.connect(lambda p=popup: self._on_explain_requested(p))
        popup.pin_toggled.connect(self._sync_dismiss_watch)

        def _gone() -> None:
            if popup in self._popups:
                self._popups.remove(popup)
            self._sync_dismiss_watch()

        popup.destroyed.connect(_gone)
        self._sync_dismiss_watch()

    def _sync_dismiss_watch(self, *_args) -> None:
        if any(not p.is_pinned for p in self._popups):
            self.dismiss.start()
        else:
            self.dismiss.stop()

    def _on_global_press(self) -> None:
        """全局按下/滚轮：关掉所有"点击点不在其内"的未钉住弹窗。"""
        if QApplication.activePopupWidget() is not None:
            return  # 自家复制菜单展开中，别把这次点击当"点外部"
        # 不用监听回调给的坐标（Windows 上是物理像素），读 QCursor.pos() 与
        # frameGeometry() 同一逻辑坐标系；frameGeometry 含阴影留边，自带容差
        pos = QCursor.pos()
        now = time.monotonic()
        for p in list(self._popups):
            if now - getattr(p, "_born_at", 0.0) < self.POPUP_BIRTH_GRACE_S:
                continue
            if not p.is_pinned and p.isVisible() and not p.frameGeometry().contains(pos):
                p.close()

    def _on_foreground_changed(self) -> None:
        """前台应用换了（Windows 轮询路径，含纯键盘 Alt+Tab）：未钉住弹窗全收走。

        换成自家窗口（点了弹窗/主窗）不算离开。"""
        if QApplication.activePopupWidget() is not None:
            return
        from .ui.dismiss_watch import foreground_window_id

        fg = foreground_window_id()
        own = set()
        for w in [self.window, *self._popups]:
            try:
                own.add(int(w.winId()))
            except RuntimeError:
                pass  # 已被 Qt 销毁
        if fg and fg in own:
            return
        now = time.monotonic()
        for p in list(self._popups):
            if now - getattr(p, "_born_at", 0.0) < self.POPUP_BIRTH_GRACE_S:
                continue
            if not p.is_pinned and p.isVisible():
                p.close()

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
        """截图翻译（弹窗式）。"""
        self._start_capture("popup")

    def trigger_screenshot_inplace(self) -> None:
        """截图翻译（原位式）：译文直接盖在原文位置上。"""
        self._start_capture("inplace")

    def _start_capture(self, mode: str) -> None:
        if self._overlay is not None:
            return
        self._capture_mode = mode
        self._overlay = CaptureOverlay()
        self._overlay.region_selected.connect(self._on_region_selected)
        self._overlay.cancelled.connect(self._clear_overlay)
        self._overlay.start()

    def _clear_overlay(self) -> None:
        self._overlay = None

    def _start_ocr(self, image, rect: QRect, mode: str) -> None:
        thread = OcrThread(self.bridge, image, rect, mode)
        self._ocr_threads = [t for t in self._ocr_threads if t.is_alive()]
        self._ocr_threads.append(thread)
        thread.start()

    def _on_region_selected(self, rect: QRect, pixmap: QPixmap) -> None:
        if self._capture_mode == "inplace":
            self._clear_overlay()
            self._start_inplace(rect, pixmap)
            return
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
        self._start_ocr(pixmap.toImage(), rect, "popup")

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

    # ---------- 原位截图翻译 ----------

    def _start_inplace(self, rect: QRect, pixmap: QPixmap) -> None:
        from .ui.inplace_overlay import InPlaceOverlay

        # 同一时刻只留一层：否则连按几次热键会在屏幕上叠一摞置顶窗，
        # 关掉最上面那层，下面几层还在，看起来就是"关不掉"
        self._close_inplace()
        # 裁剪图是物理像素、覆盖层用逻辑坐标，两者之比就是这块屏的缩放
        dpr = pixmap.width() / rect.width() if rect.width() else 1.0
        overlay = InPlaceOverlay(rect, pixmap, dpr)
        # 只有当前这层关闭才清引用（旧层的迟到信号不能把新层的引用抹掉）
        overlay.closed.connect(
            lambda o=overlay: setattr(self, "_inplace", None)
            if self._inplace is o else None)
        overlay.popup_requested.connect(self._on_inplace_popup)
        self._inplace = overlay
        overlay.start()
        self._start_ocr(pixmap.toImage(), rect, "inplace")

    def _on_blocks_ready(self, blocks: list, anchor: QRect) -> None:
        from .free_engine import resolve_engine
        from .ocr import merge_near_blocks
        from .translator import BlockTranslateWorker

        overlay = self._inplace
        if overlay is None:  # 用户已按 Esc 关掉，不再打扰
            return
        # OCR 的分段偏碎（行距一超过 0.8 倍行高就断），贴回去会是一堆小卡片；
        # 原位模式按更宽松的间距合并，视觉上更接近"原文那一段"
        blocks = merge_near_blocks(blocks, gap_factor=1.8)
        overlay.prepare(blocks)
        texts = [b.text for b in blocks]
        source = "\n\n".join(texts)
        try:
            client = resolve_engine(self.cfg)
        except LLMError as e:
            overlay.fail(str(e), 3000)
            return
        target = self._resolve_target(source, self.cfg.get("screenshot.target_language", ""))
        worker = BlockTranslateWorker(
            client, texts, target, self.cfg.get("translate.style", "general"),
        )
        self._workers.append(worker)
        results: dict = {}

        def block_done(idx: int, text: str) -> None:
            if self._inplace is not overlay:
                return
            results[idx] = text
            overlay.set_block_text(idx, text)

        def all_done() -> None:
            if self._inplace is not overlay or not results:
                return
            overlay.finish()
            full = "\n\n".join(results[i] for i in sorted(results))
            self.window.add_history(source, full, target,
                                    self.cfg.get("translate.style", "general"))

        worker.block_done.connect(block_done)
        worker.block_failed.connect(
            lambda idx, msg: overlay.fail(f"翻译失败：{msg}", 3000))
        worker.finished_all.connect(all_done)
        worker.finished.connect(
            lambda w=worker: self._workers.remove(w) if w in self._workers else None)
        overlay.closed.connect(worker.cancel)
        worker.start()

    def _on_inplace_popup(self, source: str, result: str, rect: QRect) -> None:
        """原位工具条点"弹窗"：已完成的原文/译文转成对照弹窗（不重新翻译）。"""
        # 先收覆盖层：它正持有前台，留到最后关会让"前台变化"检测误杀新弹窗
        self._close_inplace()
        popup = TranslationPopup(original=source, show_original=True,
                                 width=int(self.cfg.get("ui.popup_width", 520)),
                                 show_explain=self._explain_available())
        self._track_popup(popup)
        popup.set_done(result)
        popup.show_near(rect)

    def _on_blocks_failed(self, message: str, anchor: QRect) -> None:
        if self._inplace is not None:
            self._inplace.fail(f"识别失败：{message}", 2500)

    def _close_inplace(self) -> None:
        overlay = self._inplace
        self._inplace = None
        if overlay is not None:
            try:
                overlay.close()
            except RuntimeError:
                pass  # 已被 Qt 销毁

    # ---------- 首次引导 ----------

    def _maybe_onboard(self) -> None:
        if self.cfg.get("onboarded", False):
            return
        self.cfg.set("onboarded", True)
        self.cfg.save()
        from PySide6.QtWidgets import QMessageBox

        from .platform_ui import double_copy_label, pretty_hotkey

        box = QMessageBox(self.window)
        box.setWindowTitle("欢迎使用 Ivyea Translate")
        box.setIcon(QMessageBox.Information)
        box.setText(
            "三步上手：\n\n"
            f"1. 选中任意文字，按 {double_copy_label()}（连按两下 C）—— 立即翻译\n"
            f"2. 按 {pretty_hotkey(self.cfg.get('hotkeys.screenshot_translate', ''))} "
            "框选屏幕 —— 截图翻译（弹窗显示译文）\n"
            f"3. 按 {pretty_hotkey(self.cfg.get('hotkeys.screenshot_inplace', ''))} "
            "框选屏幕 —— 原位翻译（译文直接盖在原文上，工具条可复制/看原文，Esc 关闭）\n"
            "4. 免配置即用（内置免费翻译）；到「设置」填自己的大模型可解锁风格与邮件助手\n\n"
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
        """一键更新：下载(进度条)→静默安装→自动重启。非安装版引导到官网。"""
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        from PySide6.QtWidgets import QMessageBox, QProgressDialog
        from .updater import UpdateDownloader, apply_update_and_quit, is_installed_copy

        if not is_installed_copy():
            QMessageBox.information(
                self.window, "更新",
                "当前安装方式无法自替换，请到官网下载新版覆盖使用。")
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

    def _shutdown_workers(self, timeout_ms: int = 1500) -> None:
        self._shutdown_ocr()
        """取消并等待所有后台翻译线程结束。

        cancel() 只置标志位，线程要等当前这一片 SSE 读完才看得到；流式片段来得
        很密，通常几十毫秒就退出。留一个总预算兜底：真卡在网络读上（对端不回包）
        就不再干等，直接 terminate——反正下一步就是进程退出。
        """
        workers = [w for w in self._workers if w.isRunning()]
        for w in workers:
            w.cancel()
        deadline = time.monotonic() + timeout_ms / 1000
        for w in workers:
            remaining = max(0, int((deadline - time.monotonic()) * 1000))
            if not w.wait(remaining):
                log.warning("翻译线程未在预算内退出，强制终止")
                w.terminate()
                w.wait(200)

    def _shutdown_ocr(self, budget_s: float = 2.0) -> None:
        """等一下还在识别的 OCR 线程。

        它们是 daemon 线程，解释器退出时会被直接掐断；若正卡在 onnxruntime 的
        C++ 里，进程会以 abort 收场（Windows 上可能被记成崩溃）。识别通常一两秒
        就完，给个小预算等一等，超时就不管了——最坏也不比现在差。
        """
        deadline = time.monotonic() + budget_s
        for t in list(self._ocr_threads):
            if not t.is_alive():
                continue
            t.join(max(0.0, deadline - time.monotonic()))
        self._ocr_threads = [t for t in self._ocr_threads if t.is_alive()]

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
        if self.window.isMinimized():
            # 最小化状态下 show() 不会还原（窗口还在任务栏里躺着）；
            # 用位运算去掉 Minimized，最大化过的窗口还原后仍是最大化
            from PySide6.QtCore import Qt

            self.window.setWindowState(
                (self.window.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive
            )
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
