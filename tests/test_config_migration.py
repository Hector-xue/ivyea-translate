import json

from ivyea_translate.config import Config


def test_old_default_popup_width_migrated(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"ui": {"popup_width": 420}}), encoding="utf-8")
    cfg = Config(path)
    assert cfg.get("ui.popup_width") == 520


def test_custom_popup_width_kept(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"ui": {"popup_width": 600}}), encoding="utf-8")
    cfg = Config(path)
    assert cfg.get("ui.popup_width") == 600


def test_removed_keys_stripped_from_old_config(tmp_path):
    """升级后清理已删功能的残留键：呼出主窗口/划词热键/划词气泡/复制翻译。"""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "hotkeys": {"select_translate": "<ctrl>+<alt>+t", "show_main_window": "<ctrl>+<alt>+i",
                    "screenshot_translate": "<ctrl>+<alt>+s"},
        "selection_bubble": {"enabled": True},
        "clipboard_watch": {"enabled": True, "max_chars": 5000},
    }), encoding="utf-8")
    cfg = Config(path)
    assert cfg.get("hotkeys.select_translate") is None
    assert cfg.get("hotkeys.show_main_window") is None
    assert cfg.get("hotkeys.screenshot_translate") == "<ctrl>+<alt>+s"  # 保留的不动
    assert cfg.get("selection_bubble") is None
    assert cfg.get("clipboard_watch") is None
    # 旧 clipboard_watch.max_chars 迁到 double_copy
    assert cfg.get("double_copy.max_chars") == 5000


def test_screenshot_lang_defaults_to_follow_global(tmp_path):
    cfg = Config(tmp_path / "config.json")
    assert cfg.get("screenshot.target_language") == ""
    assert cfg.get("double_copy.enabled") is True
    assert cfg.get("double_copy.max_chars") == 3000
