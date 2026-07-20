"""平台相关的界面文案。

划词触发本身跨平台（clipboard_watch.is_double_copy 只看剪贴板内容），
但"复制"键在 macOS 上是 Command 不是 Ctrl —— 文案不跟着变，Mac 用户会
照着提示按 Ctrl+C+C，永远触发不了。热键修饰键同理（Option/Control）。

纯函数 + 显式 platform 参数，便于在 Linux 上单测两个平台的输出。
"""
from __future__ import annotations

import sys
from typing import Optional

MACOS = sys.platform == "darwin"

# 修饰键在各平台的习惯写法
_MAC_KEYS = {"ctrl": "⌃", "control": "⌃", "alt": "⌥", "option": "⌥",
             "shift": "⇧", "cmd": "⌘", "command": "⌘", "super": "⌘", "win": "⌘"}
_PC_KEYS = {"ctrl": "Ctrl", "control": "Ctrl", "alt": "Alt", "option": "Alt",
            "shift": "Shift", "cmd": "Win", "command": "Win", "super": "Win", "win": "Win"}


def _is_mac(macos: Optional[bool]) -> bool:
    # 默认值不能写成 macos=MACOS：默认参数在 def 时求值，会把平台判断固化，
    # 测试里改 MACOS 也不生效（同类默认参数坑之前踩过）
    return MACOS if macos is None else macos


def copy_modifier(macos: Optional[bool] = None) -> str:
    """复制键的修饰键名（Mac 是 Command）。"""
    return "⌘" if _is_mac(macos) else "Ctrl"


def double_copy_label(macos: Optional[bool] = None) -> str:
    """划词手势文案：Ctrl+C+C / ⌘+C+C。"""
    return f"{copy_modifier(macos)}+C+C"


def pretty_hotkey(combo: str, macos: Optional[bool] = None) -> str:
    """"<ctrl>+<alt>+s" -> "Ctrl + Alt + S"（Mac 上 -> "⌃ + ⌥ + S"）。"""
    if not combo:
        return ""
    keys = _MAC_KEYS if _is_mac(macos) else _PC_KEYS
    parts = []
    for raw in combo.split("+"):
        token = raw.strip().strip("<>").lower()
        if not token:
            continue
        parts.append(keys.get(token, token.upper() if len(token) == 1 else token.title()))
    return " + ".join(parts)
