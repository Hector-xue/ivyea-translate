"""设计令牌 + 全局 QSS。

设计取向对标 Linear / Raycast / Notion 这类桌面工具：**中性底色 + 白色卡片 +
一处克制的品牌绿**。老版本把整扇窗铺成饱和的绿渐变、卡片又是半透明白，导致底与
卡几乎同色，什么都糊在一起、没有层次；现在底色收成近中性的暖白（只在角落留一点
品牌绿的呼吸感），卡片改纯白 + 细描边，输入框反过来做成"凹陷"的浅灰绿，
层级立刻分明：底 < 卡 < 控件。
"""
from __future__ import annotations

from pathlib import Path

# ---- 设计令牌（品牌绿来自 logo，其余为中性梯度） ----
# 窗体底：近中性暖白，右下角带一点点品牌绿，避免死白也不抢卡片
SHELL_GRADIENT = (
    "qlineargradient(x1:0, y1:0, x2:0.7, y2:1, "
    "stop:0 #FCFDFA, stop:0.55 #F7FAF3, stop:1 #EFF5E7)"
)
BG_GRADIENT = SHELL_GRADIENT  # 兼容旧引用
CARD_BG = "#FFFFFF"
CARD_BORDER = "#E7EEDF"
FIELD_BG = "#F7F9F4"        # 输入类控件比卡片更"凹"一层
FIELD_BORDER = "#E4EBDB"
FIELD_BG_FOCUS = "#FFFFFF"
GLASS_CARD = CARD_BG        # 兼容旧引用
GLASS_BORDER = CARD_BORDER
POPUP_BG = "rgba(255, 255, 255, 0.98)"
ACCENT = "#6BA53F"          # logo 主绿
ACCENT_HOVER = "#5B9334"
ACCENT_SOFT = "rgba(107, 165, 63, 0.12)"
ACCENT_RING = "rgba(107, 165, 63, 0.22)"
TEXT_PRIMARY = "#2F3B29"    # 深叶绿灰
TEXT_SECONDARY = "#8A9682"
TEXT_MUTED = "#A9B3A2"
DANGER = "#E5484D"
OK = "#3AA675"

WINDOW_RADIUS = 14          # 窗体圆角（自绘外壳）
RADIUS = 14                 # 卡片
RADIUS_SM = 10              # 控件
SHADOW_RGB = (46, 66, 36)   # 投影颜色（带一点绿，融进品牌）
SHADOW_ALPHA = 52           # 最内圈描边透明度上限
# 输入框/下拉框内文字距控件左边缘的距离（12px padding + 1px 边框）：
# 控件下方的说明文字按这个值内缩，才会和控件里的文字左对齐
FIELD_TEXT_INSET = 13


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
/* 窗口本体透明：看得见的那扇窗是 Shell（自绘外壳负责投影） */
QMainWindow, QWidget#Root {{
    background: transparent;
}}
QWidget#Shell {{
    background: {SHELL_GRADIENT};
    border: 1px solid rgba(255, 255, 255, 0.9);
    border-radius: {WINDOW_RADIUS}px;
}}
/* ---- 卡片 ---- */
QWidget#GlassCard {{
    background: {CARD_BG};
    border: 1px solid {CARD_BORDER};
    border-radius: {RADIUS}px;
}}
QLabel#CardTitle {{
    font-size: 13px;
    font-weight: 600;
    color: {TEXT_PRIMARY};
    background: transparent;
}}
QLabel#Hint {{
    color: {TEXT_SECONDARY};
    font-size: 12px;
    background: transparent;
}}
/* 表单里紧贴控件下方的说明：左侧内缩到与控件内文字同一条竖线上 */
QLabel#FieldHint {{
    color: {TEXT_SECONDARY};
    font-size: 12px;
    background: transparent;
    padding-left: {FIELD_TEXT_INSET}px;
}}
QLabel {{
    background: transparent;
    font-size: 13px;
}}
/* ---- 标题栏 ---- */
QWidget#TitleBar {{
    background: transparent;
}}
QLabel#Wordmark {{
    color: {TEXT_PRIMARY};
    background: transparent;
}}
QPushButton#WinBtn, QPushButton#WinBtnClose {{
    background: transparent;
    border: none;
    border-radius: 7px;
    padding: 0;
    color: {TEXT_SECONDARY};
    font-size: 13px;
}}
QPushButton#WinBtn:hover {{
    background: rgba(47, 59, 41, 0.07);
    color: {TEXT_PRIMARY};
}}
QPushButton#WinBtnClose:hover {{
    background: {DANGER};
    color: white;
}}
/* ---- 输入类：比卡片更"凹"一层，聚焦时提亮 + 品牌绿描边 ---- */
QPlainTextEdit, QTextEdit, QLineEdit {{
    background: {FIELD_BG};
    border: 1px solid {FIELD_BORDER};
    border-radius: {RADIUS_SM}px;
    padding: 9px 12px;
    font-size: 14px;
    selection-background-color: {ACCENT};
    selection-color: white;
}}
QLineEdit {{
    min-height: 22px;  /* 防止高分屏/全屏下输入框塌矮裁字 */
}}
QPlainTextEdit:focus, QTextEdit:focus, QLineEdit:focus {{
    background: {FIELD_BG_FOCUS};
    border: 1px solid {ACCENT};
}}
QPlainTextEdit:hover, QTextEdit:hover, QLineEdit:hover {{
    border-color: #D6E2C9;
}}
/* ---- 按钮 ---- */
QPushButton {{
    background: {CARD_BG};
    border: 1px solid {CARD_BORDER};
    border-radius: {RADIUS_SM}px;
    padding: 8px 16px;
    font-size: 13px;
    color: {TEXT_PRIMARY};
}}
QPushButton:hover {{
    background: #FBFDF8;
    border-color: #CFDFC0;
}}
QPushButton:pressed {{
    background: #F2F6EC;
}}
QPushButton#Primary {{
    background: {ACCENT};
    color: white;
    border: 1px solid {ACCENT};
    font-weight: 600;
    padding: 8px 20px;
}}
QPushButton#Primary:hover {{
    background: {ACCENT_HOVER};
    border-color: {ACCENT_HOVER};
}}
QPushButton#Primary:pressed {{
    background: #4F812C;
}}
QPushButton#Primary:disabled {{
    background: #BFD6AB;
    border-color: #BFD6AB;
    color: rgba(255, 255, 255, 0.9);
}}
QPushButton#Ghost {{
    background: transparent;
    border: none;
    color: {TEXT_SECONDARY};
    padding: 5px 9px;
    border-radius: 8px;
}}
QPushButton#Ghost:hover {{
    background: {ACCENT_SOFT};
    color: {ACCENT_HOVER};
}}
/* ---- 下拉框 ---- */
QComboBox {{
    background: {FIELD_BG};
    border: 1px solid {FIELD_BORDER};
    border-radius: {RADIUS_SM}px;
    padding: 6px 12px;
    font-size: 13px;
    min-height: 22px;
}}
QComboBox:hover {{
    border-color: #D6E2C9;
}}
QComboBox:focus, QComboBox:on {{
    border-color: {ACCENT};
    background: {FIELD_BG_FOCUS};
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
    background: #FFFFFF;
    border: 1px solid {CARD_BORDER};
    border-radius: {RADIUS_SM}px;
    padding: 5px;
    selection-background-color: transparent;
    outline: none;
}}
QComboBox QAbstractItemView::item {{
    min-height: 28px;
    padding: 5px 10px;
    margin: 1px 2px;
    border-radius: 7px;
    color: {TEXT_PRIMARY};
}}
QComboBox QAbstractItemView::item:hover {{
    background: {ACCENT_SOFT};
    color: {ACCENT_HOVER};
}}
QComboBox QAbstractItemView::item:selected {{
    background: {ACCENT};
    color: white;
}}
QCheckBox {{
    spacing: 8px;
    font-size: 13px;
    background: transparent;
}}
QCheckBox::indicator {{
    width: 17px;
    height: 17px;
    border-radius: 5px;
    border: 1px solid #C7D2BC;
    background: #FFFFFF;
}}
QCheckBox::indicator:hover {{
    border-color: {ACCENT};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}
/* ---- 滚动条：细、悬停才明显 ---- */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: rgba(47, 59, 41, 0.16);
    border-radius: 4px;
    min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{
    background: rgba(47, 59, 41, 0.3);
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}
/* ---- 页签：分段控件（选中项是一枚白色药丸） ---- */
QTabWidget::pane {{
    border: none;
    background: transparent;
}}
QTabBar {{
    background: transparent;
    qproperty-drawBase: 0;
}}
QTabBar::tab {{
    background: transparent;
    color: {TEXT_SECONDARY};
    padding: 6px 16px;
    margin-right: 4px;
    border: 1px solid transparent;
    border-radius: 9px;
    font-size: 13px;
}}
QTabBar::tab:hover {{
    background: rgba(47, 59, 41, 0.05);
    color: {TEXT_PRIMARY};
}}
QTabBar::tab:selected {{
    background: {CARD_BG};
    border: 1px solid {CARD_BORDER};
    color: {ACCENT};
    font-weight: 600;
}}
/* ---- 历史列表 ---- */
QListWidget {{
    background: transparent;
    border: none;
    outline: none;
}}
QListWidget::item {{
    background: {CARD_BG};
    border-radius: {RADIUS_SM}px;
    padding: 10px 12px;
    margin: 4px 2px;
    color: {TEXT_PRIMARY};
}}
QListWidget::item:selected {{
    background: {ACCENT_SOFT};
    border: 1px solid {ACCENT};
}}
/* 历史页：用自绘卡片(HistRow)，条目本身透明避免双层卡 */
QListWidget#HistList::item {{
    background: transparent;
    border: none;
    padding: 0;
    margin: 0;
    color: {TEXT_SECONDARY};
}}
QWidget#HistRow {{
    background: {CARD_BG};
    border: 1px solid {CARD_BORDER};
    border-radius: {RADIUS_SM}px;
}}
QWidget#HistRow:hover {{
    border-color: {ACCENT};
    background: #FCFEF9;
}}
QLabel#HistMeta {{ color: {TEXT_MUTED}; font-size: 11px; }}
QLabel#HistSrc {{ color: {TEXT_SECONDARY}; font-size: 13px; }}
QLabel#HistRes {{ color: {TEXT_PRIMARY}; font-size: 14px; font-weight: 500; }}
QToolTip {{
    background: white;
    color: {TEXT_PRIMARY};
    border: 1px solid {CARD_BORDER};
    border-radius: 7px;
    padding: 4px 8px;
}}
"""
