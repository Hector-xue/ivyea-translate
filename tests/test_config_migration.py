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


def test_selection_bubble_default_on(tmp_path):
    cfg = Config(tmp_path / "config.json")
    assert cfg.get("selection_bubble.enabled") is True


def test_old_default_hotkey_migrated(tmp_path):
    import json
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hotkeys": {"select_translate": "<ctrl>+<alt>+t",
                                            "show_main_window": "<ctrl>+<alt>+i"}}), encoding="utf-8")
    cfg = Config(path)
    assert cfg.get("hotkeys.select_translate") == "<ctrl>+<alt>+x"
    assert cfg.get("hotkeys.show_main_window") is None


def test_custom_hotkey_kept(tmp_path):
    import json
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hotkeys": {"select_translate": "<ctrl>+<shift>+q"}}), encoding="utf-8")
    cfg = Config(path)
    assert cfg.get("hotkeys.select_translate") == "<ctrl>+<shift>+q"


def test_screenshot_lang_defaults_to_follow_global(tmp_path):
    cfg = Config(tmp_path / "config.json")
    assert cfg.get("screenshot.target_language") == ""
    assert cfg.get("double_copy.enabled") is True
