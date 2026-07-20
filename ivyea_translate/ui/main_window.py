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
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import config as cfgmod
from ..config import Config, LANGUAGES, PROVIDER_PRESETS, STYLES
from ..llm import LLMError, client_from_config
from ..translator import TranslateWorker
from . import theme


class QComboBox(QComboBox):  # noqa: F811  —— 全模块下拉框统一为"悬停滚轮不改值"
    """悬停时滚轮不改选项（避免误触），并把滚轮交给父级滚动页面；
    下拉列表用 QListView 以保证 QSS 美化生效。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)  # 去掉 WheelFocus：滚轮不再聚焦到它
        self.setView(QListView())
        # 允许随窗口收缩（默认下拉框不肯低于内容宽度，会把整排顶宽导致横向溢出）；
        # 窄到放不下时当前项文字自动省略号，拉宽后恢复
        self.setMinimumWidth(64)  # 关键：允许被压缩（否则最小=内容宽，顶宽整排）
        from PySide6.QtWidgets import QSizePolicy

        # Preferred：有空间时保持内容宽（左对齐紧凑），窗口变窄时收缩并省略号
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def wheelEvent(self, event):
        event.ignore()  # 不消费滚轮 -> 冒泡给 QScrollArea 滚动页面


def _glass_card() -> QWidget:
    card = QWidget()
    card.setObjectName("GlassCard")
    return card


def _scrollable(inner: QWidget) -> QScrollArea:
    """把页面包进滚动容器：窗口变小时整页滚动，不再挤压/溢出/裁切。"""
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QScrollArea.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    sa.setStyleSheet(
        "QScrollArea { background: transparent; }"
        " QScrollArea > QWidget > QWidget { background: transparent; }"
    )
    sa.setWidget(inner)
    return sa


class _ElideLabel(QLabel):
    """单行标签：文本超宽时右侧省略号，且不会把最小宽度顶大（避免横向溢出）。"""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = text
        super().setText(text)

    def setFullText(self, text: str) -> None:
        self._full = text
        self._apply()

    def _apply(self) -> None:
        fm = self.fontMetrics()
        super().setText(fm.elidedText(self._full, Qt.ElideRight, max(16, self.width())))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply()

    def minimumSizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(16, super().minimumSizeHint().height())


class _HistoryRow(QWidget):
    """历史条目卡片：时间/语言（弱）+ 原文（弱）+ 译文（醒目），双击回填。"""

    activated = Signal()

    def __init__(self, meta: str, source: str, result: str, parent=None):
        super().__init__(parent)
        self.setObjectName("HistRow")
        self.setAttribute(Qt.WA_StyledBackground, True)  # 否则 QSS 卡片背景/边框不绘制
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 9, 14, 11)
        v.setSpacing(3)
        m = QLabel(meta)
        m.setObjectName("HistMeta")
        s = _ElideLabel(source)
        s.setObjectName("HistSrc")
        r = _ElideLabel(result)
        r.setObjectName("HistRes")
        for w in (m, s, r):
            w.setAttribute(Qt.WA_TransparentForMouseEvents, True)  # 双击落到卡片
            v.addWidget(w)

    def mouseDoubleClickEvent(self, event):
        self.activated.emit()


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
        self.resize(820, 780)
        self.setMinimumSize(520, 420)  # 允许自由缩小；设置页有滚动容器兜底
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
        tabs.addTab(self._build_email_tab(), "写作")
        tabs.addTab(self._build_history_tab(), "历史")
        tabs.addTab(self._build_settings_tab(), "设置")
        outer.addWidget(tabs, 1)

    # ================= 翻译页 =================

    def _build_translate_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 12, 8, 12)
        lay.setSpacing(12)

        card = _glass_card()
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(18, 16, 18, 18)
        card_lay.setSpacing(10)

        # 语言/风格选择行
        opts = QHBoxLayout()
        opts.addWidget(QLabel("目标语言"))
        self.lang_combo = QComboBox()
        self.lang_combo.addItem(self._auto_label(), "auto")  # 智能方向
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
        self.source_edit.setMinimumHeight(88)
        self.source_edit.setMaximumHeight(180)  # 源文区不喧宾夺主，长文本内部滚动
        card_lay.addWidget(self.source_edit)

        btn_row = QHBoxLayout()
        hint = QLabel("选中文字后按 Ctrl+C+C 即翻译 · 截图翻译 {shot}".format(
            shot=self._pretty_hotkey(self.cfg.get("hotkeys.screenshot_translate", "")),
        ))
        hint.setObjectName("Hint")
        hint.setWordWrap(True)  # 窄窗自动换行，避免长文案顶宽导致横向溢出
        btn_row.addWidget(hint, 1)
        btn_row.addStretch(1)
        self.translate_btn = QPushButton("翻译")
        self.translate_btn.setObjectName("Primary")
        self.translate_btn.setMinimumWidth(120)
        self.translate_btn.clicked.connect(self._on_translate_clicked)
        btn_row.addWidget(self.translate_btn)
        card_lay.addLayout(btn_row)

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
        self.result_view.setMinimumHeight(220)  # 译文区始终有足够高度浏览
        self.result_view.setPlaceholderText("译文会出现在这里")
        res_lay.addWidget(self.result_view, 1)

        lay.addWidget(card)            # 源文区：紧凑
        lay.addWidget(result_card, 1)  # 译文区：占据剩余空间

        self.source_edit.installEventFilter(self)
        self._on_lang_style_changed()
        return _scrollable(page)

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

    @staticmethod
    def _lang_label(code: str) -> str:
        return dict(LANGUAGES).get(code, code)

    def _auto_label(self) -> str:
        primary = self._lang_label(self.cfg.get("translate.primary_language", "zh-CN"))
        secondary = self._lang_label(self.cfg.get("translate.secondary_language", "en"))
        return f"自动（{primary} ↔ {secondary}）"

    def _resolve_target_lang(self, text: str, setting: str) -> str:
        if setting == "auto":
            from ..langdetect import choose_target

            return choose_target(
                text,
                self.cfg.get("translate.primary_language", "zh-CN"),
                self.cfg.get("translate.secondary_language", "en"),
            )
        return setting

    def _on_lang_style_changed(self) -> None:
        lang = self.lang_combo.currentData()
        style = self.style_combo.currentData()
        if style in ("american", "british") and lang not in ("en", "auto"):
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
        from ..free_engine import resolve_engine

        try:
            client = resolve_engine(self.cfg)
        except LLMError as e:
            self.result_view.setPlainText(str(e))
            return
        self.result_view.setPlainText("")
        self.translate_btn.setEnabled(False)
        self.translate_btn.setText("翻译中…")
        style = self.style_combo.currentData()
        target = self._resolve_target_lang(text, self.lang_combo.currentData())
        self._worker = TranslateWorker(client, text, target, style, parent=self)
        self._worker.chunk.connect(self._append_result)
        self._worker.finished_ok.connect(lambda full: self._translate_done(text, full, target, style))
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

    # ================= 邮件页 =================

    def _build_email_tab(self) -> QWidget:
        from ..translator import EMAIL_TONES

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 12, 8, 12)
        lay.setSpacing(12)

        card = _glass_card()
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(18, 16, 18, 18)
        card_lay.setSpacing(10)

        from ..translator import COMPOSE_SCENARIOS

        opts = QHBoxLayout()
        opts.addWidget(QLabel("场景"))
        self.email_scenario_combo = QComboBox()
        for code, (label, _desc, _sub) in COMPOSE_SCENARIOS.items():
            self.email_scenario_combo.addItem(label, code)
        self._select_combo_data(self.email_scenario_combo, self.cfg.get("email.scenario", "email"))
        self.email_scenario_combo.currentIndexChanged.connect(self._on_scenario_changed)
        opts.addWidget(self.email_scenario_combo)
        opts.addSpacing(10)
        opts.addWidget(QLabel("目标语言"))
        self.email_lang_combo = QComboBox()
        for code, label in LANGUAGES:
            self.email_lang_combo.addItem(label, code)
        self._select_combo_data(self.email_lang_combo, self.cfg.get("email.target_language", "en"))
        opts.addWidget(self.email_lang_combo)
        opts.addSpacing(10)
        opts.addWidget(QLabel("语气"))
        self.email_tone_combo = QComboBox()
        for code, (label, _rule) in EMAIL_TONES.items():
            self.email_tone_combo.addItem(label, code)
        self._select_combo_data(self.email_tone_combo, self.cfg.get("email.tone", "business"))
        opts.addWidget(self.email_tone_combo)
        opts.addStretch(1)
        card_lay.addLayout(opts)

        self.email_hint = QLabel("")
        self.email_hint.setObjectName("Hint")
        self.email_hint.setWordWrap(True)
        card_lay.addWidget(self.email_hint)

        self.email_source = QPlainTextEdit()
        self.email_source.setMinimumHeight(88)
        self.email_source.setMaximumHeight(180)  # 草稿区不喧宾夺主，长文本内部滚动
        card_lay.addWidget(self.email_source)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.email_btn = QPushButton("生成")
        self.email_btn.setObjectName("Primary")
        self.email_btn.setMinimumWidth(130)
        self.email_btn.clicked.connect(self._on_email_clicked)
        btn_row.addWidget(self.email_btn)
        card_lay.addLayout(btn_row)

        result_card = _glass_card()
        res_lay = QVBoxLayout(result_card)
        res_lay.setContentsMargins(18, 14, 18, 16)
        res_lay.setSpacing(8)

        # 主题行（仅邮件场景显示）
        self.email_subject_row = QWidget()
        subj_row = QHBoxLayout(self.email_subject_row)
        subj_row.setContentsMargins(0, 0, 0, 0)
        subj_label = QLabel("主题")
        subj_label.setObjectName("CardTitle")
        subj_row.addWidget(subj_label)
        self.email_subject = QLineEdit()
        self.email_subject.setReadOnly(True)
        self.email_subject.setPlaceholderText("自动总结的邮件主题会出现在这里")
        subj_row.addWidget(self.email_subject, 1)
        copy_subj_btn = QPushButton("复制主题")
        copy_subj_btn.setObjectName("Ghost")
        copy_subj_btn.clicked.connect(lambda: self._copy_text(self.email_subject.text()))
        subj_row.addWidget(copy_subj_btn)
        res_lay.addWidget(self.email_subject_row)

        body_head = QHBoxLayout()
        self.email_body_label = QLabel("正文")
        self.email_body_label.setObjectName("CardTitle")
        body_head.addWidget(self.email_body_label)
        body_head.addStretch(1)
        copy_body_btn = QPushButton("复制")
        copy_body_btn.setObjectName("Ghost")
        copy_body_btn.clicked.connect(lambda: self._copy_text(self.email_body.toPlainText()))
        body_head.addWidget(copy_body_btn)
        res_lay.addLayout(body_head)
        self.email_body = QPlainTextEdit()
        self.email_body.setReadOnly(True)
        self.email_body.setMinimumHeight(150)  # 防止被主题/回译挤成一两行
        self.email_body.setPlaceholderText("生成的地道外语会出现在这里")
        res_lay.addWidget(self.email_body, 1)

        # 回译校对：把生成结果译回母语，确认意思没跑偏
        self.email_backtrans_label = QLabel("回译校对")
        self.email_backtrans_label.setObjectName("Hint")
        res_lay.addWidget(self.email_backtrans_label)
        self.email_backtrans = QPlainTextEdit()
        self.email_backtrans.setReadOnly(True)
        self.email_backtrans.setPlaceholderText("生成后自动把结果译回母语，供你确认含义")
        self.email_backtrans.setMaximumHeight(96)
        self.email_backtrans.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 13px;")
        res_lay.addWidget(self.email_backtrans)

        lay.addWidget(card)            # 草稿区：紧凑
        lay.addWidget(result_card, 1)  # 结果区：占据剩余空间
        self._on_scenario_changed()
        return _scrollable(page)

    def _on_scenario_changed(self) -> None:
        from ..translator import COMPOSE_SCENARIOS

        scen = self.email_scenario_combo.currentData()
        _name, _desc, want_subject = COMPOSE_SCENARIOS.get(scen, COMPOSE_SCENARIOS["general"])
        self.email_subject_row.setVisible(want_subject)
        hints = {
            "email": "用母语写要点，生成目标语言的地道邮件并自动拟主题",
            "message": "用母语写想说的，生成地道的外语聊天消息（Slack / 微信 / Teams）",
            "comment": "用母语写意见，生成简洁专业的外语评论 / PR 留言",
            "social": "用母语写想法，生成适合社媒的地道外语贴文",
            "general": "用母语写下要点，生成地道的目标语言文字",
        }
        placeholders = {
            "email": "例如：告诉客户发货推迟三天，物流拥堵导致，表达歉意并给 5% 折扣",
            "message": "例如：跟同事说接口改好了，让他有空一起联调下",
            "comment": "例如：这函数没处理空值，建议加判断；另外命名可以更清晰",
            "social": "例如：分享我做了个免费划词翻译小工具，欢迎试用",
            "general": "用母语写下你想表达的内容…",
        }
        self.email_hint.setText(hints.get(scen, hints["general"]))
        self.email_source.setPlaceholderText(placeholders.get(scen, placeholders["general"]))
        self.email_body_label.setText("正文" if want_subject else "生成结果")
        self.cfg.set("email.scenario", scen)
        self.cfg.save()

    def _copy_text(self, text: str) -> None:
        if text:
            from PySide6.QtGui import QGuiApplication
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app and hasattr(app, "mark_own_copy"):
                app.mark_own_copy(text)
            QGuiApplication.clipboard().setText(text)

    def _on_email_clicked(self) -> None:
        from ..translator import build_compose_messages

        text = self.email_source.toPlainText().strip()
        if not text:
            return
        if getattr(self, "_email_worker", None) is not None and self._email_worker.isRunning():
            self._email_worker.cancel()
        # 写作助手需要大模型的改写能力，免费翻译引擎无法胜任
        try:
            client = client_from_config(self.cfg)
        except LLMError:
            self.email_body.setPlainText(
                "写作助手需要配置大模型：请到「设置 → 翻译模型」填写 API Key。\n"
                "（免费翻译引擎只做直译，不支持改写与润色。）"
            )
            return
        scen = self.email_scenario_combo.currentData()
        lang = self.email_lang_combo.currentData()
        tone = self.email_tone_combo.currentData()
        self.cfg.set("email.scenario", scen)
        self.cfg.set("email.target_language", lang)
        self.cfg.set("email.tone", tone)
        self.cfg.save()
        self.email_subject.clear()
        self.email_body.setPlainText("")
        self.email_backtrans.setPlainText("")
        self.email_btn.setEnabled(False)
        self.email_btn.setText("生成中…")
        self._email_worker = TranslateWorker(
            client, text, lang, "general", parent=self,
            messages=build_compose_messages(text, lang, scen, tone),
        )
        self._email_worker.chunk.connect(self._append_email_chunk)
        self._email_worker.finished_ok.connect(lambda full, s=text, sc=scen: self._email_done(s, full, sc))
        self._email_worker.failed.connect(self._email_failed)
        self._email_worker.start()

    def _append_email_chunk(self, piece: str) -> None:
        self.email_body.moveCursor(self.email_body.textCursor().MoveOperation.End)
        self.email_body.insertPlainText(piece)

    def _email_done(self, source: str, full: str, scenario: str) -> None:
        from ..translator import parse_compose_output

        subject, body = parse_compose_output(full, scenario)
        self.email_subject.setText(subject)
        self.email_body.setPlainText(body)
        self.email_btn.setEnabled(True)
        self.email_btn.setText("生成")
        self.add_history(source, (f"【主题】{subject}\n{body}" if subject else body),
                         self.email_lang_combo.currentData(), "compose")
        # 回译校对：把结果译回母语，确认意思没跑偏
        if bool(self.cfg.get("email.backtranslate", True)) and body.strip():
            self._run_backtranslation(body)

    def _run_backtranslation(self, body: str) -> None:
        try:
            client = client_from_config(self.cfg)
        except LLMError:
            return
        primary = self.cfg.get("translate.primary_language", "zh-CN")
        self.email_backtrans.setPlainText("回译中…")
        self._bt_worker = TranslateWorker(client, body, primary, "general", parent=self)
        self._bt_worker.finished_ok.connect(lambda full: self.email_backtrans.setPlainText(full))
        self._bt_worker.failed.connect(lambda m: self.email_backtrans.setPlainText(""))
        self._bt_worker.start()

    def _email_failed(self, message: str) -> None:
        self.email_btn.setEnabled(True)
        self.email_btn.setText("优化并翻译")
        self.email_body.setPlainText(message)

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
        self.history_list.setObjectName("HistList")
        self.history_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # 不再有丑横条
        self.history_list.setSelectionMode(QListWidget.NoSelection)
        self.history_list.setSpacing(6)
        self.history_list.setFrameShape(QListWidget.NoFrame)
        self.history_list.setUniformItemSizes(False)
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
        from PySide6.QtCore import QSize

        self.history_list.clear()
        if not self._history:
            item = QListWidgetItem("还没有翻译记录")
            item.setFlags(Qt.NoItemFlags)
            item.setTextAlignment(Qt.AlignCenter)
            self.history_list.addItem(item)
            return
        lang_names = dict(LANGUAGES)
        for entry in self._history:
            src = entry["source"].replace("\n", " ").strip()
            res = entry["result"].replace("\n", " ").strip()
            code = entry.get("lang", "")
            lang_label = "自动" if code == "auto" else lang_names.get(code, code)
            meta = f"{entry['ts']}    {lang_label}" if lang_label else entry["ts"]
            row = _HistoryRow(meta, src, "→ " + res)
            row.activated.connect(lambda e=entry: self._activate_entry(e))
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, row.sizeHint().height()))
            self.history_list.addItem(item)
            self.history_list.setItemWidget(item, row)

    def _activate_entry(self, entry: dict) -> None:
        self.source_edit.setPlainText(entry["source"])
        self.result_view.setPlainText(entry["result"])
        self.centralWidget().findChild(QTabWidget).setCurrentIndex(0)

    def _clear_history(self) -> None:
        self._history = []
        self._save_history()
        self._refresh_history_list()

    # ================= 设置页 =================

    def _build_settings_tab(self) -> QWidget:
        # 外层滚动容器：窗口缩小/全屏拉伸时表单保持自然高度，不被压矮或抻高
        from PySide6.QtWidgets import QScrollArea

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; } QScrollArea > QWidget > QWidget { background: transparent; }")
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 12, 8, 12)
        lay.setSpacing(12)
        scroll.setWidget(page)

        # 翻译引擎卡
        eng_card = _glass_card()
        ec = QVBoxLayout(eng_card)
        ec.setContentsMargins(18, 14, 18, 16)
        et = QLabel("翻译引擎")
        et.setObjectName("CardTitle")
        ec.addWidget(et)
        eng_form = QFormLayout()
        eng_form.setHorizontalSpacing(14)
        eng_form.setVerticalSpacing(10)
        self.engine_combo = QComboBox()
        self.engine_combo.addItem("自动（未配置模型时用免费翻译）", "auto")
        self.engine_combo.addItem("免费翻译（无需配置，开箱即用）", "free")
        self.engine_combo.addItem("我的大模型", "llm")
        self._select_combo_data(self.engine_combo, self.cfg.get("translate.engine", "auto"))
        eng_form.addRow("引擎", self.engine_combo)
        eng_hint = QLabel(
            "免费翻译基于公开翻译接口，无需 API Key 即可直接使用；\n"
            "配置大模型可获得更高质量、风格控制与邮件助手。"
        )
        eng_hint.setObjectName("Hint")
        eng_hint.setWordWrap(True)
        eng_form.addRow("", eng_hint)
        # 自动互译语言对（目标语言选"自动"时在这两者间智能切换）
        pair_row = QHBoxLayout()
        self.primary_lang_combo = QComboBox()
        self.secondary_lang_combo = QComboBox()
        for code, label in LANGUAGES:
            self.primary_lang_combo.addItem(label, code)
            self.secondary_lang_combo.addItem(label, code)
        self._select_combo_data(self.primary_lang_combo, self.cfg.get("translate.primary_language", "zh-CN"))
        self._select_combo_data(self.secondary_lang_combo, self.cfg.get("translate.secondary_language", "en"))
        pair_row.addWidget(self.primary_lang_combo, 1)
        arrow = QLabel("↔")
        pair_row.addWidget(arrow)
        pair_row.addWidget(self.secondary_lang_combo, 1)
        eng_form.addRow("自动互译语言", pair_row)
        pair_hint = QLabel("目标语言设为「自动」时：选中前者的文本→翻成后者，其余→翻成前者。")
        pair_hint.setObjectName("Hint")
        pair_hint.setWordWrap(True)
        eng_form.addRow("", pair_hint)
        ec.addLayout(eng_form)
        lay.addWidget(eng_card)

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
        self.dblcopy_check = QCheckBox("选中文字后按 Ctrl+C+C 即翻译")
        self.dblcopy_check.setChecked(bool(self.cfg.get("double_copy.enabled", True)))
        hk_form.addRow("划词翻译", self.dblcopy_check)
        self.hk_shot_edit = QLineEdit(self.cfg.get("hotkeys.screenshot_translate", ""))
        hk_form.addRow("截图翻译快捷键", self.hk_shot_edit)
        hk_hint = QLabel('格式如 <ctrl>+<alt>+s（尖括号包修饰键）')
        hk_hint.setObjectName("Hint")
        hk_form.addRow("", hk_hint)
        self.shot_lang_combo = QComboBox()
        self.shot_lang_combo.addItem("跟随全局目标语言", "")
        for code, label in LANGUAGES:
            self.shot_lang_combo.addItem(label, code)
        self._select_combo_data(self.shot_lang_combo, self.cfg.get("screenshot.target_language", ""))
        hk_form.addRow("截图翻译目标语言", self.shot_lang_combo)
        self.hotkey_status = QLabel("")
        self.hotkey_status.setObjectName("Hint")
        self.hotkey_status.setWordWrap(True)
        hk_form.addRow("状态", self.hotkey_status)
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
        return scroll

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
        feed = getattr(self, "_update_feed", None)
        if not feed:
            return
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None and hasattr(app, "_start_update"):
            app._start_update(feed)  # 统一走 app 的一键更新（进度条→静默安装→重启）

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

        from ..free_engine import resolve_engine

        def run():
            try:
                engine = resolve_engine(self.cfg)
                reply = engine.test_connection()
                if getattr(engine, "is_free", False):
                    msg, ok = reply, True  # 已含"免费引擎可用（…）"
                else:
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
        # 引擎选择也在此落盘：测试连接与保存都会先调用本方法
        self.cfg.set("translate.engine", self.engine_combo.currentData())
        self.cfg.set("provider.preset", self.preset_combo.currentData())
        self.cfg.set("provider.base_url", self.base_url_edit.text().strip())
        self.cfg.set("provider.api_key", self.api_key_edit.text().strip())
        self.cfg.set("provider.model", self.model_edit.text().strip())

    def _on_save_settings(self) -> None:
        self._flush_provider_fields()
        self.cfg.set("hotkeys.screenshot_translate", self.hk_shot_edit.text().strip())
        self.cfg.set("double_copy.enabled", self.dblcopy_check.isChecked())
        self.cfg.set("screenshot.target_language", self.shot_lang_combo.currentData())
        self.cfg.set("translate.primary_language", self.primary_lang_combo.currentData())
        self.cfg.set("translate.secondary_language", self.secondary_lang_combo.currentData())
        self.cfg.save()
        # 主/次语言改动后刷新翻译页"自动"项的显示文案
        if hasattr(self, "lang_combo"):
            idx = self.lang_combo.findData("auto")
            if idx >= 0:
                self.lang_combo.setItemText(idx, self._auto_label())
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
