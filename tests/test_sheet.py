"""应用内提示卡（替代系统 QMessageBox）：结果回传、键盘、以及 app 里各条提示路径能真正建出来。"""
import types

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget


def _host(qapp):
    host = QWidget()
    host.resize(800, 600)
    host.show()
    return host


def test_button_click_reports_key_and_removes_sheet(qapp):
    from ivyea_translate.ui.sheet import Sheet

    host = _host(qapp)
    got = []
    sheet = Sheet.show_on(host, "标题", "正文", [("later", "以后", "ghost"), ("now", "立即更新", "primary")])
    sheet.finished.connect(got.append)
    assert sheet.geometry() == host.rect()
    sheet.button("now").click()
    assert got == ["now"] and sheet.isHidden()


def test_escape_dismisses_only_when_allowed(qapp):
    from ivyea_translate.ui.sheet import DISMISS, Sheet

    host = _host(qapp)
    got = []
    locked = Sheet.show_on(host, "更新中", None, [("cancel", "取消", "ghost")], dismissible=False)
    locked.finished.connect(got.append)
    QTest.keyClick(locked, Qt.Key_Escape)
    assert got == [] and locked.isVisible()
    locked.close_with("cancel")

    free = Sheet.show_on(host, "提示", "x", [("ok", "好", "primary")])
    free.finished.connect(got.append)
    QTest.keyClick(free, Qt.Key_Escape)
    assert got == ["cancel", DISMISS]


def test_enter_triggers_primary(qapp):
    from ivyea_translate.ui.sheet import Sheet

    host = _host(qapp)
    got = []
    sheet = Sheet.show_on(host, "t", "b", [("later", "以后", "ghost"), ("now", "更新", "primary")])
    sheet.finished.connect(got.append)
    QTest.keyClick(sheet, Qt.Key_Return)
    assert got == ["now"]


def test_card_stays_inside_small_host(qapp):
    from ivyea_translate.ui.sheet import Sheet

    host = QWidget()
    host.resize(320, 500)
    host.show()
    sheet = Sheet.show_on(host, "t", "一段比较长的正文" * 10, [("ok", "好", "primary")])
    assert sheet.card.geometry().left() >= 0 and sheet.card.geometry().right() <= host.width()


def test_keycaps_split_both_hotkey_formats(qapp):
    from PySide6.QtWidgets import QLabel

    from ivyea_translate.ui.sheet import keycaps

    box = keycaps("Ctrl + Alt + S")
    assert [w.text() for w in box.findChildren(QLabel)] == ["Ctrl", "Alt", "S"]
    box = keycaps("Ctrl+C+C")
    assert [w.text() for w in box.findChildren(QLabel)] == ["Ctrl", "C", "C"]


def _fake_app(qapp, tmp_path):
    from ivyea_translate.app import TranslateApp
    from ivyea_translate.config import Config
    from ivyea_translate.ui.main_window import MainWindow

    cfg = Config(tmp_path / "config.json")
    win = MainWindow(cfg)
    win.show()
    fake = types.SimpleNamespace(cfg=cfg, window=win, tray=None, show_main_window=win.show)
    for name in ("_sheet", "_maybe_onboard", "_prompt_update", "_start_update"):
        setattr(fake, name, types.MethodType(getattr(TranslateApp, name), fake))
    return fake


def _sheets(win):
    from ivyea_translate.ui.sheet import Sheet

    return [s for s in win.shell.findChildren(Sheet) if s.isVisible()]


def test_onboarding_is_an_in_window_sheet_shown_once(qapp, tmp_path):
    fake = _fake_app(qapp, tmp_path)
    fake._maybe_onboard()
    sheets = _sheets(fake.window)
    assert len(sheets) == 1 and "欢迎" in sheets[0].title.text()
    sheets[0].close_with("ok")
    fake._maybe_onboard()
    assert _sheets(fake.window) == []


def test_update_prompt_and_portable_copy_paths_build(qapp, tmp_path, monkeypatch):
    fake = _fake_app(qapp, tmp_path)
    started = []
    fake._start_update = lambda feed: started.append(feed["version"])
    fake._prompt_update({"version": "9.9.9", "notes": "修复若干问题"})
    sheet = _sheets(fake.window)[0]
    assert "9.9.9" in sheet.title.text()
    sheet.button("now").click()
    assert started == ["9.9.9"]

    from ivyea_translate import updater
    from ivyea_translate.app import TranslateApp

    monkeypatch.setattr(updater, "is_installed_copy", lambda: False)
    TranslateApp._start_update(fake, {"version": "9.9.9", "setup_url": "x"})
    assert "官网" in _sheets(fake.window)[0].title.text()
