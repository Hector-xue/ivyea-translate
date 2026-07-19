"""主窗口：翻译 / 历史 / 设置 三页签，粉彩玻璃风格。"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import config as cfgmod
from ..config import Config, LANGUAGES, PROVIDER_PRESETS, STYLES
from ..llm import LLMError, client_from_config
from ..translator import TranslateWorker
from . import theme


def _glass_card() -> QWidget:
    card = QWidget()
    card.setObjectName("GlassCard")
    return card


class MainWindow(QMainWindow):
    settings_saved = Signal()
    # 测试连接在后台线程跑，结果必须经信号回主线程；
    # 之前用 QTimer.singleShot(0,...) 在非 Qt 线程启动定时器，回调永不执行，
    # 界面会永远停在"测试中…"
    _test_finished = Signal(str, bool)

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        # 关窗默认藏到托盘；真正退出前由 app 置 True，否则 closeEvent 的
        # ignore() 会把 QEvent::Quit 触发的 closeAllWindows 拒掉，导致退出被取消
        self.really_quit = False
        self._worker: Optional[TranslateWorker] = None
        self._history_path = cfg.path.parent / "history.json"
        self._history: List[dict] = self._load_history()

        self.setWindowTitle("Ivyea Translate")
        self.resize(760, 640)
        self._test_finished.connect(self._show_test_result)

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(24, 20, 24, 24)
        outer.setSpacing(14)

        # 顶栏
        head = QHBoxLayout()
        dot = QLabel()
        logo = theme.asset_path("logo.png")
        if logo:
            from PySide6.QtGui import QPixmap

            dot.setPixmap(QPixmap(logo).scaled(
                26, 26, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            dot.setText("●")
            dot.setStyleSheet(f"color: {theme.ACCENT}; font-size: 16px;")
        title = QLabel("Ivyea Translate · 随手即译")
        f = QFont()
        f.setPointSize(13)
        f.setBold(True)
        title.setFont(f)
        self.head_status = QLabel("")
        self.head_status.setObjectName("Hint")
        head.addWidget(dot)
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.head_status)
        outer.addLayout(head)

        tabs = QTabWidget()
        tabs.addTab(self._build_translate_tab(), "翻译")
        tabs.addTab(self._build_history_tab(), "历史")
        tabs.addTab(self._build_settings_tab(), "设置")
        outer.addWidget(tabs, 1)

    # ================= 翻译页 =================

    def _build_translate_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(12)

        card = _glass_card()
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(18, 16, 18, 18)
        card_lay.setSpacing(10)

        # 语言/风格选择行
        opts = QHBoxLayout()
        opts.addWidget(QLabel("目标语言"))
        self.lang_combo = QComboBox()
        for code, label in LANGUAGES:
            self.lang_combo.addItem(label, code)
        self._select_combo_data(self.lang_combo, self.cfg.get("translate.target_language"))
        self.lang_combo.currentIndexChanged.connect(self._on_lang_style_changed)
        opts.addWidget(self.lang_combo)
        opts.addSpacing(12)
        opts.addWidget(QLabel("风格"))
        self.style_combo = QComboBox()
        for code, label in STYLES:
            self.style_combo.addItem(label, code)
        self._select_combo_data(self.style_combo, self.cfg.get("translate.style"))
        self.style_combo.currentIndexChanged.connect(self._on_lang_style_changed)
        opts.addWidget(self.style_combo)
        self.style_hint = QLabel("")
        self.style_hint.setObjectName("Hint")
        opts.addWidget(self.style_hint)
        opts.addStretch(1)
        card_lay.addLayout(opts)

        self.source_edit = QPlainTextEdit()
        self.source_edit.setPlaceholderText("输入或粘贴要翻译的内容…（Ctrl+Enter 翻译）")
        self.source_edit.setMinimumHeight(120)
        card_lay.addWidget(self.source_edit)

        btn_row = QHBoxLayout()
        hint = QLabel("划词翻译 {sel} · 截图翻译 {shot}".format(
            sel=self._pretty_hotkey(self.cfg.get("hotkeys.select_translate", "")),
            shot=self._pretty_hotkey(self.cfg.get("hotkeys.screenshot_translate", "")),
        ))
        hint.setObjectName("Hint")
        btn_row.addWidget(hint)
        btn_row.addStretch(1)
        self.translate_btn = QPushButton("翻译")
        self.translate_btn.setObjectName("Primary")
        self.translate_btn.setMinimumWidth(120)
        self.translate_btn.clicked.connect(self._on_translate_clicked)
        btn_row.addWidget(self.translate_btn)
        card_lay.addLayout(btn_row)
        lay.addWidget(card)

        result_card = _glass_card()
        res_lay = QVBoxLayout(result_card)
        res_lay.setContentsMargins(18, 14, 18, 16)
        res_head = QHBoxLayout()
        rt = QLabel("译文")
        rt.setObjectName("CardTitle")
        res_head.addWidget(rt)
        res_head.addStretch(1)
        self.copy_result_btn = QPushButton("复制译文")
        self.copy_result_btn.setObjectName("Ghost")
        self.copy_result_btn.clicked.connect(self._copy_result)
        res_head.addWidget(self.copy_result_btn)
        res_lay.addLayout(res_head)
        self.result_view = QPlainTextEdit()
        self.result_view.setReadOnly(True)
        self.result_view.setPlaceholderText("译文会出现在这里")
        res_lay.addWidget(self.result_view, 1)
        lay.addWidget(result_card, 1)

        self.source_edit.installEventFilter(self)
        self._on_lang_style_changed()
        return page

    def eventFilter(self, obj, event):
        if obj is self.source_edit and event.type() == event.Type.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ControlModifier:
                self._on_translate_clicked()
                return True
        return super().eventFilter(obj, event)

    @staticmethod
    def _select_combo_data(combo: QComboBox, data) -> None:
        idx = combo.findData(data)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    @staticmethod
    def _pretty_hotkey(combo: str) -> str:
        return combo.replace("<", "").replace(">", "").replace("+", " + ").title()

    def _on_lang_style_changed(self) -> None:
        lang = self.lang_combo.currentData()
        style = self.style_combo.currentData()
        if style in ("american", "british") and lang != "en":
            self.style_hint.setText("（美式/英式仅目标为英语时生效）")
        else:
            self.style_hint.setText("")
        self.cfg.set("translate.target_language", lang)
        self.cfg.set("translate.style", style)
        self.cfg.save()

    def _on_translate_clicked(self) -> None:
        text = self.source_edit.toPlainText().strip()
        if not text:
            return
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
        try:
            client = client_from_config(self.cfg)
        except LLMError as e:
            self.result_view.setPlainText(str(e))
            return
        self.result_view.setPlainText("")
        self.translate_btn.setEnabled(False)
        self.translate_btn.setText("翻译中…")
        lang = self.lang_combo.currentData()
        style = self.style_combo.currentData()
        self._worker = TranslateWorker(client, text, lang, style, parent=self)
        self._worker.chunk.connect(self._append_result)
        self._worker.finished_ok.connect(lambda full: self._translate_done(text, full, lang, style))
        self._worker.failed.connect(self._translate_failed)
        self._worker.start()

    def _append_result(self, piece: str) -> None:
        self.result_view.moveCursor(self.result_view.textCursor().MoveOperation.End)
        self.result_view.insertPlainText(piece)

    def _translate_done(self, source: str, result: str, lang: str, style: str) -> None:
        self.translate_btn.setEnabled(True)
        self.translate_btn.setText("翻译")
        self.add_history(source, result, lang, style)

    def _translate_failed(self, message: str) -> None:
        self.translate_btn.setEnabled(True)
        self.translate_btn.setText("翻译")
        self.result_view.setPlainText(message)

    def _copy_result(self) -> None:
        text = self.result_view.toPlainText()
        if text:
            from PySide6.QtGui import QGuiApplication
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app and hasattr(app, "mark_own_copy"):
                app.mark_own_copy(text)
            QGuiApplication.clipboard().setText(text)

    # ================= 历史页 =================

    def _build_history_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 12, 0, 0)
        head = QHBoxLayout()
        tip = QLabel("最近翻译（双击回填到翻译页）")
        tip.setObjectName("Hint")
        head.addWidget(tip)
        head.addStretch(1)
        clear_btn = QPushButton("清空")
        clear_btn.setObjectName("Ghost")
        clear_btn.clicked.connect(self._clear_history)
        head.addWidget(clear_btn)
        lay.addLayout(head)
        self.history_list = QListWidget()
        self.history_list.itemDoubleClicked.connect(self._on_history_activate)
        lay.addWidget(self.history_list, 1)
        self._refresh_history_list()
        return page

    def _load_history(self) -> List[dict]:
        if self._history_path.exists():
            try:
                with open(self._history_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return []

    def _save_history(self) -> None:
        try:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._history_path, "w", encoding="utf-8") as f:
                json.dump(self._history, f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    def add_history(self, source: str, result: str, lang: str, style: str) -> None:
        limit = int(self.cfg.get("ui.history_limit", 100))
        self._history.insert(0, {
            "ts": time.strftime("%Y-%m-%d %H:%M"),
            "source": source,
            "result": result,
            "lang": lang,
            "style": style,
        })
        del self._history[limit:]
        self._save_history()
        self._refresh_history_list()

    def _refresh_history_list(self) -> None:
        if not hasattr(self, "history_list"):
            return
        self.history_list.clear()
        for entry in self._history:
            src = entry["source"].replace("\n", " ")
            res = entry["result"].replace("\n", " ")
            item = QListWidgetItem(f"{entry['ts']}  {src[:40]}\n→ {res[:60]}")
            item.setData(Qt.UserRole, entry)
            self.history_list.addItem(item)

    def _on_history_activate(self, item: QListWidgetItem) -> None:
        entry = item.data(Qt.UserRole)
        self.source_edit.setPlainText(entry["source"])
        self.result_view.setPlainText(entry["result"])
        self.centralWidget().findChild(QTabWidget).setCurrentIndex(0)

    def _clear_history(self) -> None:
        self._history = []
        self._save_history()
        self._refresh_history_list()

    # ================= 设置页 =================

    def _build_settings_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(12)

        # 模型卡
        model_card = _glass_card()
        mc = QVBoxLayout(model_card)
        mc.setContentsMargins(18, 14, 18, 16)
        mt = QLabel("翻译模型（OpenAI 兼容接口）")
        mt.setObjectName("CardTitle")
        mc.addWidget(mt)
        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)

        self.preset_combo = QComboBox()
        for key, preset in PROVIDER_PRESETS.items():
            self.preset_combo.addItem(preset["label"], key)
        self._select_combo_data(self.preset_combo, self.cfg.get("provider.preset"))
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        form.addRow("服务商", self.preset_combo)

        self.base_url_edit = QLineEdit(self.cfg.get("provider.base_url", ""))
        self.base_url_edit.setPlaceholderText("https://api.deepseek.com/v1")
        form.addRow("接口地址", self.base_url_edit)

        key_row = QHBoxLayout()
        self.api_key_edit = QLineEdit(self.cfg.get("provider.api_key", ""))
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText("sk-…")
        show_btn = QPushButton("显示")
        show_btn.setObjectName("Ghost")
        show_btn.setCheckable(True)
        show_btn.toggled.connect(
            lambda on: self.api_key_edit.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password)
        )
        key_row.addWidget(self.api_key_edit, 1)
        key_row.addWidget(show_btn)
        form.addRow("API Key", key_row)

        self.model_edit = QLineEdit(self.cfg.get("provider.model", ""))
        self.model_edit.setPlaceholderText("deepseek-chat")
        form.addRow("模型名", self.model_edit)
        mc.addLayout(form)

        test_row = QHBoxLayout()
        self.test_result = QLabel("")
        self.test_result.setObjectName("Hint")
        test_row.addWidget(self.test_result, 1)
        self.test_btn = QPushButton("测试连接")
        self.test_btn.clicked.connect(self._on_test_connection)
        test_row.addWidget(self.test_btn)
        mc.addLayout(test_row)
        lay.addWidget(model_card)

        # 快捷键 + 行为卡
        hk_card = _glass_card()
        hc = QVBoxLayout(hk_card)
        hc.setContentsMargins(18, 14, 18, 16)
        ht = QLabel("快捷键与行为")
        ht.setObjectName("CardTitle")
        hc.addWidget(ht)
        hk_form = QFormLayout()
        hk_form.setHorizontalSpacing(14)
        hk_form.setVerticalSpacing(10)
        self.hk_select_edit = QLineEdit(self.cfg.get("hotkeys.select_translate", ""))
        hk_form.addRow("划词翻译", self.hk_select_edit)
        self.hk_shot_edit = QLineEdit(self.cfg.get("hotkeys.screenshot_translate", ""))
        hk_form.addRow("截图翻译", self.hk_shot_edit)
        self.hk_main_edit = QLineEdit(self.cfg.get("hotkeys.show_main_window", ""))
        hk_form.addRow("呼出主窗口", self.hk_main_edit)
        hk_hint = QLabel('格式如 <ctrl>+<alt>+t（尖括号包修饰键）')
        hk_hint.setObjectName("Hint")
        hk_form.addRow("", hk_hint)
        self.hotkey_status = QLabel("")
        self.hotkey_status.setObjectName("Hint")
        self.hotkey_status.setWordWrap(True)
        hk_form.addRow("状态", self.hotkey_status)
        self.watch_check = QCheckBox("开启复制翻译（复制任意文本后自动弹窗翻译）")
        self.watch_check.setChecked(bool(self.cfg.get("clipboard_watch.enabled", False)))
        hk_form.addRow("", self.watch_check)
        self.bubble_check = QCheckBox("开启划词气泡（选中文字后光标旁出现图标，点击即翻译）")
        self.bubble_check.setChecked(bool(self.cfg.get("selection_bubble.enabled", True)))
        hk_form.addRow("", self.bubble_check)
        hc.addLayout(hk_form)
        lay.addWidget(hk_card)

        # 关于与更新卡
        up_card = _glass_card()
        uc = QVBoxLayout(up_card)
        uc.setContentsMargins(18, 14, 18, 16)
        ut = QLabel("关于与更新")
        ut.setObjectName("CardTitle")
        uc.addWidget(ut)
        up_row = QHBoxLayout()
        from .. import __version__

        self.version_label = QLabel(f"Ivyea Translate v{__version__}")
        up_row.addWidget(self.version_label)
        self.update_status = QLabel("")
        self.update_status.setObjectName("Hint")
        up_row.addWidget(self.update_status, 1)
        self.update_btn = QPushButton("")
        self.update_btn.setObjectName("Primary")
        self.update_btn.setVisible(False)
        self.update_btn.clicked.connect(self._on_apply_update)
        up_row.addWidget(self.update_btn)
        self.check_update_btn = QPushButton("检查更新")
        self.check_update_btn.clicked.connect(self._on_check_update)
        up_row.addWidget(self.check_update_btn)
        uc.addLayout(up_row)
        lay.addWidget(up_card)

        save_row = QHBoxLayout()
        self.save_status = QLabel("")
        self.save_status.setObjectName("Hint")
        save_row.addWidget(self.save_status, 1)
        save_btn = QPushButton("保存设置")
        save_btn.setObjectName("Primary")
        save_btn.setMinimumWidth(140)
        save_btn.clicked.connect(self._on_save_settings)
        save_row.addWidget(save_btn)
        lay.addLayout(save_row)
        lay.addStretch(1)
        return page

    # ---------- 更新 ----------

    def _on_check_update(self) -> None:
        from ..updater import UpdateChecker

        self.check_update_btn.setEnabled(False)
        self.update_status.setStyleSheet("")
        self.update_status.setText("检查中…")
        self._update_checker = UpdateChecker(
            self.cfg.get("update.feed_url") or "https://translate.ivyea.com/download/version.json",
            parent=self,
        )
        self._update_checker.update_available.connect(self.show_update_available)
        self._update_checker.no_update.connect(
            lambda: (self.check_update_btn.setEnabled(True), self.update_status.setText("已是最新版本"))
        )
        self._update_checker.failed.connect(
            lambda msg: (self.check_update_btn.setEnabled(True), self.update_status.setText(msg))
        )
        self._update_checker.start()

    def show_update_available(self, feed: dict) -> None:
        """手动检查或启动时静默检查发现新版后调用。"""
        self._update_feed = feed
        self.check_update_btn.setEnabled(True)
        self.update_status.setStyleSheet(f"color: {theme.ACCENT};")
        self.update_status.setText(f"发现新版本 v{feed['version']}")
        self.update_btn.setText(f"更新到 v{feed['version']}")
        self.update_btn.setVisible(True)

    def _on_apply_update(self) -> None:
        from ..updater import UpdateDownloader, apply_update_and_quit, is_installed_copy

        feed = getattr(self, "_update_feed", None)
        if not feed:
            return
        if not is_installed_copy():
            # 便携版/源码运行：打开官网下载页
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl

            QDesktopServices.openUrl(QUrl(feed.get("page_url", "https://translate.ivyea.com/")))
            return
        self.update_btn.setEnabled(False)
        self.update_status.setText("下载中… 0%")
        self._update_dl = UpdateDownloader(feed["setup_url"], feed["version"], parent=self)
        self._update_dl.progress.connect(
            lambda pct: self.update_status.setText(f"下载中… {pct}%")
        )
        self._update_dl.failed.connect(
            lambda msg: (self.update_btn.setEnabled(True), self.update_status.setText(msg))
        )
        self._update_dl.finished_ok.connect(self._on_update_downloaded)
        self._update_dl.start()

    def _on_update_downloaded(self, setup_path: str) -> None:
        from ..updater import apply_update_and_quit
        from PySide6.QtWidgets import QApplication

        self.update_status.setText("安装中，应用即将重启…")
        app = QApplication.instance()
        quit_cb = getattr(app, "request_quit", app.quit)
        apply_update_and_quit(setup_path, quit_cb)

    def _on_preset_changed(self) -> None:
        key = self.preset_combo.currentData()
        preset = PROVIDER_PRESETS.get(key, {})
        if preset.get("base_url"):
            self.base_url_edit.setText(preset["base_url"])
        if preset.get("model"):
            self.model_edit.setText(preset["model"])

    def _on_test_connection(self) -> None:
        self._flush_provider_fields()
        self.test_btn.setEnabled(False)
        self.test_result.setText("测试中…")

        import threading

        def run():
            try:
                client = client_from_config(self.cfg)
                reply = client.test_connection()
                msg, ok = f"连通正常（模型回复：{reply[:30]}）", True
            except LLMError as e:
                msg, ok = str(e), False
            except Exception as e:
                msg, ok = f"测试失败：{e}", False
            self._test_finished.emit(msg, ok)

        threading.Thread(target=run, daemon=True).start()

    def _show_test_result(self, msg: str, ok: bool) -> None:
        self.test_btn.setEnabled(True)
        color = "#3AA675" if ok else theme.ACCENT
        self.test_result.setStyleSheet(f"color: {color};")
        self.test_result.setText(msg)

    def _flush_provider_fields(self) -> None:
        self.cfg.set("provider.preset", self.preset_combo.currentData())
        self.cfg.set("provider.base_url", self.base_url_edit.text().strip())
        self.cfg.set("provider.api_key", self.api_key_edit.text().strip())
        self.cfg.set("provider.model", self.model_edit.text().strip())

    def _on_save_settings(self) -> None:
        self._flush_provider_fields()
        self.cfg.set("hotkeys.select_translate", self.hk_select_edit.text().strip())
        self.cfg.set("hotkeys.screenshot_translate", self.hk_shot_edit.text().strip())
        self.cfg.set("hotkeys.show_main_window", self.hk_main_edit.text().strip())
        self.cfg.set("clipboard_watch.enabled", self.watch_check.isChecked())
        self.cfg.set("selection_bubble.enabled", self.bubble_check.isChecked())
        self.cfg.save()
        self.save_status.setText("已保存 ✓")
        from PySide6.QtCore import QTimer

        QTimer.singleShot(2000, lambda: self.save_status.setText(""))
        self.settings_saved.emit()

    def set_hotkey_status(self, error: Optional[str]) -> None:
        """app 注册热键后回填状态；error=None 表示全部生效。"""
        if not hasattr(self, "hotkey_status"):
            return
        if error:
            self.hotkey_status.setStyleSheet(f"color: {theme.ACCENT};")
            self.hotkey_status.setText(f"⚠ {error}")
        else:
            self.hotkey_status.setStyleSheet("color: #3AA675;")
            self.hotkey_status.setText("全局快捷键已生效")

    # 关窗只是隐藏（常驻托盘）；退出流程中必须放行
    def closeEvent(self, event):
        if self.really_quit:
            super().closeEvent(event)
            return
        event.ignore()
        self.hide()
