import pytest

from ivyea_translate.hotkeys import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    MOD_WIN,
    parse_hotkey,
)


def test_default_hotkeys_parse():
    assert parse_hotkey("<ctrl>+<alt>+t") == (MOD_CONTROL | MOD_ALT, ord("T"))
    assert parse_hotkey("<ctrl>+<alt>+s") == (MOD_CONTROL | MOD_ALT, ord("S"))
    assert parse_hotkey("<ctrl>+<alt>+i") == (MOD_CONTROL | MOD_ALT, ord("I"))


def test_modifier_aliases_and_case():
    assert parse_hotkey("<CTRL>+<Shift>+A") == (MOD_CONTROL | MOD_SHIFT, ord("A"))
    assert parse_hotkey("<win>+z") == (MOD_WIN, ord("Z"))
    assert parse_hotkey("<cmd>+z") == (MOD_WIN, ord("Z"))


def test_function_keys_and_digits():
    assert parse_hotkey("<alt>+<f2>") == (MOD_ALT, 0x71)
    assert parse_hotkey("f12") == (0, 0x7B)
    assert parse_hotkey("<ctrl>+1") == (MOD_CONTROL, ord("1"))


def test_named_keys():
    assert parse_hotkey("<ctrl>+<space>") == (MOD_CONTROL, 0x20)
    assert parse_hotkey("<ctrl>+<alt>+/") == (MOD_CONTROL | MOD_ALT, 0xBF)


@pytest.mark.parametrize("bad", ["", "<ctrl>+<alt>", "<ctrl>+t+s", "<ctrl>+<bogus>", "+"])
def test_invalid_combos_raise(bad):
    with pytest.raises(ValueError):
        parse_hotkey(bad)


def test_inplace_hotkey_is_registrable(qapp):
    """原位截图翻译要有自己的信号和默认组合，否则热键管理器会静默丢掉它。"""
    from ivyea_translate.config import DEFAULT_CONFIG
    from ivyea_translate.hotkeys import HotkeyManager

    mgr = HotkeyManager()
    names = mgr._signal_map()
    assert "screenshot_inplace" in names and "screenshot_translate" in names

    combos = DEFAULT_CONFIG["hotkeys"]
    assert combos["screenshot_inplace"] != combos["screenshot_translate"]
    for combo in combos.values():
        parse_hotkey(combo)  # 默认值必须解析得动
