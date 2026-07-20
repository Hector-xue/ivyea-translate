from ivyea_translate.platform_ui import double_copy_label, pretty_hotkey


def test_double_copy_label_uses_command_on_mac():
    # macOS 的复制键是 Command，照抄 Ctrl 文案会让 Mac 用户永远触发不了划词
    assert double_copy_label(macos=False) == "Ctrl+C+C"
    assert double_copy_label(macos=True) == "⌘+C+C"


def test_pretty_hotkey_windows():
    assert pretty_hotkey("<ctrl>+<alt>+s", macos=False) == "Ctrl + Alt + S"
    assert pretty_hotkey("<ctrl>+<shift>+f1", macos=False) == "Ctrl + Shift + F1"


def test_pretty_hotkey_mac_uses_symbols():
    assert pretty_hotkey("<ctrl>+<alt>+s", macos=True) == "⌃ + ⌥ + S"


def test_pretty_hotkey_empty():
    assert pretty_hotkey("", macos=False) == ""
    assert pretty_hotkey("", macos=True) == ""
