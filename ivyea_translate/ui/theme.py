"""设计令牌 + 全局 QSS：粉彩渐变、玻璃卡、大圆角、珊瑚点缀（对标参考图）。"""
from __future__ import annotations

from pathlib import Path

# ---- 设计令牌（取自品牌 logo 的叶绿色系） ----
BG_GRADIENT = (
    "qlineargradient(x1:0, y1:0, x2:1, y2:1, "
    "stop:0 #E9F4DF, stop:0.45 #FAFDF5, stop:1 #DEEFD2)"
)
GLASS_CARD = "rgba(255, 255, 255, 0.62)"
GLASS_BORDER = "rgba(255, 255, 255, 0.85)"
POPUP_BG = "rgba(255, 255, 255, 0.96)"
ACCENT = "#6BA53F"          # logo 主绿
ACCENT_HOVER = "#5B9334"
ACCENT_SOFT = "rgba(107, 165, 63, 0.14)"
TEXT_PRIMARY = "#3C4A34"    # 深叶绿灰
TEXT_SECONDARY = "#93A388"
RADIUS = 18
RADIUS_SM = 12


def asset_path(name: str) -> str:
    """资源文件路径，兼容 PyInstaller 冻结环境（_MEIPASS）。"""
    import sys

    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parent.parent.parent
    p = base / "assets" / name
    return str(p) if p.exists() else ""

FONT_FAMILY = '"Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC", "Segoe UI", sans-serif'

_ARROW_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="6" viewBox="0 0 10 6">'
    '<path d="M1 1 L5 5 L9 1" fill="none" stroke="#93A388" stroke-width="1.6" '
    'stroke-linecap="round" stroke-linejoin="round"/></svg>'
)


def _arrow_icon_url() -> str:
    """QSS 不支持 data: URI，落一个 SVG 文件供 url() 引用（路径用正斜杠）。"""
    from ..config import CONFIG_DIR

    path = Path(CONFIG_DIR) / "assets" / "arrow-down.svg"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text(encoding="utf-8") != _ARROW_SVG:
            path.write_text(_ARROW_SVG, encoding="utf-8")
    except OSError:
        return ""
    return path.as_posix()


def app_qss() -> str:
    arrow = _arrow_icon_url()
    arrow_rule = f'image: url("{arrow}");' if arrow else "image: none;"
    return f"""
* {{
    font-family: {FONT_FAMILY};
    color: {TEXT_PRIMARY};
}}
QMainWindow, QWidget#Root {{
    background: {BG_GRADIENT};
}}
QWidget#GlassCard {{
    background: {GLASS_CARD};
    border: 1px solid {GLASS_BORDER};
    border-radius: {RADIUS}px;
}}
QLabel#CardTitle {{
    font-size: 15px;
    font-weight: 600;
    background: transparent;
}}
QLabel#Hint {{
    color: {TEXT_SECONDARY};
    font-size: 12px;
    background: transparent;
}}
QLabel {{
    background: transparent;
}}
QPlainTextEdit, QTextEdit, QLineEdit {{
    background: rgba(255, 255, 255, 0.75);
    border: 1px solid rgba(255, 255, 255, 0.9);
    border-radius: {RADIUS_SM}px;
    padding: 8px 12px;
    font-size: 14px;
    selection-background-color: {ACCENT};
    selection-color: white;
}}
QLineEdit {{
    min-height: 24px;  /* 防止高分屏/全屏下输入框塌矮裁字 */
}}
QPlainTextEdit:focus, QTextEdit:focus, QLineEdit:focus {{
    border: 1px solid {ACCENT};
}}
QPushButton {{
    background: rgba(255, 255, 255, 0.7);
    border: 1px solid rgba(255, 255, 255, 0.9);
    border-radius: {RADIUS_SM}px;
    padding: 8px 18px;
    font-size: 13px;
}}
QPushButton:hover {{
    background: rgba(255, 255, 255, 0.95);
    border-color: {ACCENT};
}}
QPushButton#Primary {{
    background: {ACCENT};
    color: white;
    border: none;
    font-weight: 600;
}}
QPushButton#Primary:hover {{
    background: {ACCENT_HOVER};
}}
QPushButton#Primary:disabled {{
    background: rgba(244, 132, 95, 0.45);
    color: rgba(255, 255, 255, 0.85);
}}
QPushButton#Ghost {{
    background: transparent;
    border: none;
    color: {TEXT_SECONDARY};
    padding: 4px 8px;
}}
QPushButton#Ghost:hover {{
    color: {ACCENT};
}}
QComboBox {{
    background: rgba(255, 255, 255, 0.75);
    border: 1px solid rgba(255, 255, 255, 0.9);
    border-radius: {RADIUS_SM}px;
    padding: 6px 12px;
    font-size: 13px;
    min-height: 22px;
}}
QComboBox:hover {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox::down-arrow {{
    {arrow_rule}
    width: 10px;
    height: 6px;
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: white;
    border: 1px solid rgba(0, 0, 0, 0.06);
    border-radius: {RADIUS_SM}px;
    padding: 4px;
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT_PRIMARY};
    outline: none;
}}
QCheckBox {{
    spacing: 8px;
    font-size: 13px;
    background: transparent;
}}
QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border-radius: 6px;
    border: 1px solid {TEXT_SECONDARY};
    background: rgba(255, 255, 255, 0.8);
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: rgba(154, 154, 181, 0.45);
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {ACCENT};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}
QTabBar::tab {{
    background: transparent;
    color: {TEXT_SECONDARY};
    padding: 8px 18px;
    font-size: 14px;
    border: none;
}}
QTabBar::tab:selected {{
    color: {ACCENT};
    font-weight: 600;
    border-bottom: 2px solid {ACCENT};
}}
QTabWidget::pane {{
    border: none;
    background: transparent;
}}
QListWidget {{
    background: transparent;
    border: none;
    outline: none;
}}
QListWidget::item {{
    background: rgba(255, 255, 255, 0.55);
    border-radius: {RADIUS_SM}px;
    padding: 10px 12px;
    margin: 4px 2px;
    color: {TEXT_PRIMARY};
}}
QListWidget::item:selected {{
    background: {ACCENT_SOFT};
    border: 1px solid {ACCENT};
}}
QToolTip {{
    background: white;
    color: {TEXT_PRIMARY};
    border: 1px solid rgba(0, 0, 0, 0.08);
    border-radius: 6px;
    padding: 4px 8px;
}}
"""
