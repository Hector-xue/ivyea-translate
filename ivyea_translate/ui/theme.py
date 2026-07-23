"""设计令牌 + 全局 QSS + 主题调色板注册表。

设计取向对标 Linear / Raycast / Notion 这类桌面工具：**中性底色 + 白色卡片 +
一处克制的品牌绿**。老版本把整扇窗铺成饱和的绿渐变、卡片又是半透明白，导致底与
卡几乎同色，什么都糊在一起、没有层次；现在底色收成近中性的暖白（只在角落留一点
品牌绿的呼吸感），卡片改纯白 + 细描边，输入框反过来做成"凹陷"的浅灰绿，
层级立刻分明：底 < 卡 < 控件。

**多主题**：上面那套是默认主题「常春藤」的取向，其余五套（爱国风 / 星海 / 樱花 /
赛博 / 雪山）沿用同一套层级关系，只换调色板与背景照片。

实现上刻意保留了"模块级常量"这种最土的形态：`ACCENT`/`CARD_BG`/`TEXT_PRIMARY`…
全模块共二十几个名字散落在 popup / inplace_overlay / titlebar / capture_overlay 里，
且几乎都在函数体内的 f-string 里现取。于是 `apply()` 只要 **重绑定这些全局名**，
所有消费方下次构造时自然拿到新主题的值 —— 一行调用方代码都不用改。
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

# ============================ 调色板 ============================


def _hex2rgb(h: str) -> Tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _mix(c1: str, c2: str, t: float) -> str:
    """c1 → c2 线性插值，t=0 全 c1。"""
    a, b = _hex2rgb(c1), _hex2rgb(c2)
    return "#%02X%02X%02X" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _rgba(color: str, alpha: float) -> str:
    r, g, b = _hex2rgb(color)
    return f"rgba({r}, {g}, {b}, {alpha})"


# 每套主题只写核心色，其余（hover/pressed/soft/滚动条…）按同一组关系推导，
# 保证六套主题的"层级手感"一致，不会这套按钮很跳、那套按钮很闷。
_THEMES: Dict[str, dict] = {
    "ivy": {
        "label": "常春藤",
        "slogan": "随手即译",
        "sub": "像常春藤一样，攀着你的每一次阅读生长",
        "motion": "ivy",
        "dark": False,
        "accent": "#6BA53F", "accent_hover": "#5B9334", "accent_pressed": "#4F812C",
        "ink": "#2F3B29", "ink2": "#8A9682", "ink3": "#A9B3A2",
        "base": ("#FCFDFA", "#F7FAF3", "#EFF5E7"),
        "card": "#FFFFFF", "card_border": "#E7EEDF",
        "field": "#F7F9F4", "field_border": "#E4EBDB",
        "shadow": (46, 66, 36),
        "veil": (255, 255, 255, 0.74),   # 内容区的纱：越大越淡，卡片才浮得起来
        "veil_top": 0.20,                # 横幅段的薄纱：照片要看得清
        "bg_focus": 0.42,                # cover 裁切的纵向锚点（0=贴顶）
        "blur": 3, "card_alpha": 0.93,
        "hero_ink": "#FFFFFF", "hero_sub": "#E8F3DC",
    },
    "patriot": {
        "label": "爱国风",
        "slogan": "山河为证",
        "sub": "以中国话，讲清楚世界的话",
        "motion": "flag",
        "dark": False,
        "accent": "#C8102E", "accent_hover": "#AE0C27", "accent_pressed": "#8F0A20",
        "ink": "#3A2320", "ink2": "#8C6F68", "ink3": "#B09A94",
        "base": ("#FFFCF8", "#FDF6EF", "#F8E9DD"),
        "card": "#FFFFFF", "card_border": "#F0DFD2",
        "field": "#FDF7F2", "field_border": "#EFE0D5",
        "shadow": (92, 34, 24),
        "veil": (255, 250, 244, 0.76),
        "veil_top": 0.22,
        "bg_focus": 0.30,
        "blur": 3, "card_alpha": 0.93,
        "hero_ink": "#FFF3D6", "hero_sub": "#FFDCC4",
    },
    "starfield": {
        "label": "星海",
        "slogan": "跨越语言的光年",
        "sub": "哈勃看见的宇宙，和你看见的句子",
        "motion": "stars",
        "dark": True,
        "accent": "#7BA9FF", "accent_hover": "#96BAFF", "accent_pressed": "#5F92F0",
        "ink": "#E8EDFA", "ink2": "#9AA6C4", "ink3": "#6F7B99",
        "base": ("#0A0E1C", "#0C1226", "#131A33"),
        "card": "#161D33", "card_border": "#28314D",
        "field": "#101728", "field_border": "#2A3350",
        "shadow": (2, 4, 12),
        "veil": (10, 14, 28, 0.70),
        "veil_top": 0.16,
        "bg_focus": 0.36,
        "blur": 0, "card_alpha": 0.90,
        "hero_ink": "#F2F5FF", "hero_sub": "#A9B7DC",
    },
    "sakura": {
        "label": "樱花",
        "slogan": "春风十里",
        "sub": "把想说的话，译成刚好落下的那一片",
        "motion": "petals",
        "dark": False,
        "accent": "#E36397", "accent_hover": "#D14E85", "accent_pressed": "#B93F71",
        "ink": "#402B33", "ink2": "#94737F", "ink3": "#B79AA4",
        "base": ("#FFFCFD", "#FEF5F8", "#FBE7EE"),
        "card": "#FFFFFF", "card_border": "#F4DFE7",
        "field": "#FEF7FA", "field_border": "#F3E1E8",
        "shadow": (96, 48, 66),
        "veil": (255, 250, 252, 0.74),
        "veil_top": 0.18,
        "bg_focus": 0.40,
        "blur": 3, "card_alpha": 0.93,
        "hero_ink": "#FFFFFF", "hero_sub": "#FFE3EE",
    },
    "cyber": {
        "label": "赛博",
        "slogan": "夜里也在翻译",
        "sub": "霓虹之下，语言不过是另一种电流",
        "motion": "neon",
        "dark": True,
        "accent": "#2DE2E6", "accent_hover": "#5AEFF2", "accent_pressed": "#1FC5C9",
        "ink": "#E6F6FF", "ink2": "#8FA6BF", "ink3": "#63788F",
        "base": ("#070A14", "#0A0F1E", "#101A2E"),
        "card": "#121A2B", "card_border": "#243349",
        "field": "#0C1424", "field_border": "#26374F",
        "shadow": (0, 6, 14),
        "veil": (7, 10, 20, 0.68),
        "veil_top": 0.14,
        "bg_focus": 0.44,
        "blur": 0, "card_alpha": 0.90,
        "hero_ink": "#EAFDFF", "hero_sub": "#7FE3E8",
    },
    "alpine": {
        "label": "雪山",
        "slogan": "清晨第一缕光",
        "sub": "像晨雾散开那样，把意思看清楚",
        "motion": "fog",
        "dark": False,
        "accent": "#3E7FA8", "accent_hover": "#356E92", "accent_pressed": "#2C5C7C",
        "ink": "#26333D", "ink2": "#71828F", "ink3": "#9AA8B3",
        "base": ("#FDFEFF", "#F5F9FC", "#E7EFF6"),
        "card": "#FFFFFF", "card_border": "#DFE8F0",
        "field": "#F6FAFD", "field_border": "#DCE6EF",
        "shadow": (30, 48, 62),
        "veil": (250, 253, 255, 0.74),
        "veil_top": 0.18,
        "bg_focus": 0.46,
        "blur": 2, "card_alpha": 0.93,
        "hero_ink": "#FFFFFF", "hero_sub": "#DCEBF7",
    },
    # ---- 两套纯色主题：不用照片、不跑动效，要的就是安静 ----
    "mint": {
        "label": "清绿",
        "slogan": "随手即译",
        "sub": "一点品牌绿，安静地待在一边",
        "motion": "",
        "photo": False,
        "dark": False,
        "accent": "#6BA53F", "accent_hover": "#5B9334", "accent_pressed": "#4F812C",
        "ink": "#2C3828", "ink2": "#7C8A74", "ink3": "#A3AE9C",
        "base": ("#FCFDFA", "#F4F9EC", "#E8F1DB"),
        "card": "#FFFFFF", "card_border": "#E3EBD9",
        "field": "#F6F9F1", "field_border": "#E2E9D8",
        "shadow": (46, 66, 36),
        "veil": (255, 255, 255, 0.0),
        "veil_top": 0.0,
        "blur": 0, "card_alpha": 1.0, "bg_focus": 0.5,
        "hero_ink": "#2C3828", "hero_sub": "#7C8A74",
    },
    "midnight": {
        "label": "墨夜",
        "slogan": "安静地读，安静地译",
        "sub": "不刺眼的深色，盯一整天也不累",
        "motion": "",
        "photo": False,
        "dark": True,
        "accent": "#7FC15A", "accent_hover": "#93D06E", "accent_pressed": "#6DAD4A",
        "ink": "#E7EBE4", "ink2": "#97A293", "ink3": "#6C776A",
        "base": ("#12161A", "#161B21", "#1C232B"),
        "card": "#1B2128", "card_border": "#2B333C",
        "field": "#151A20", "field_border": "#2E3740",
        "shadow": (0, 0, 0),
        "veil": (18, 22, 26, 0.0),
        "veil_top": 0.0,
        "blur": 0, "card_alpha": 1.0, "bg_focus": 0.5,
        "hero_ink": "#E7EBE4", "hero_sub": "#97A293",
    },
}

DEFAULT_THEME = "ivy"
_ACTIVE = DEFAULT_THEME


def theme_keys() -> List[str]:
    return list(_THEMES)


def theme_label(key: str) -> str:
    return _THEMES.get(key, _THEMES[DEFAULT_THEME])["label"]


def current() -> str:
    return _ACTIVE


def spec(key: str = "") -> dict:
    """当前（或指定）主题的完整描述：文案、动效标识、深浅、资源目录。"""
    return _THEMES.get(key or _ACTIVE, _THEMES[DEFAULT_THEME])


def _tokens(key: str) -> dict:
    """把核心色推导成全套设计令牌。"""
    t = _THEMES[key]
    dark = t["dark"]
    accent, card, ink = t["accent"], t["card"], t["ink"]
    base0, base1, base2 = t["base"]
    # 深色主题里"更凹一层"是压暗，浅色主题里是压灰
    sink = "#000000" if dark else ink
    lift = "#FFFFFF" if dark else "#FFFFFF"
    return {
        "SHELL_GRADIENT": (
            "qlineargradient(x1:0, y1:0, x2:0.7, y2:1, "
            f"stop:0 {base0}, stop:0.55 {base1}, stop:1 {base2})"
        ),
        "BG_GRADIENT": (
            "qlineargradient(x1:0, y1:0, x2:0.7, y2:1, "
            f"stop:0 {base0}, stop:0.55 {base1}, stop:1 {base2})"
        ),
        "CARD_BG": _rgba(card, t["card_alpha"]),
        "CARD_BORDER": t["card_border"],
        "GLASS_CARD": _rgba(card, t["card_alpha"]),
        "GLASS_BORDER": t["card_border"],
        "FIELD_BG": t["field"],
        "FIELD_BORDER": t["field_border"],
        "FIELD_BG_FOCUS": _mix(card, lift, 0.0) if not dark else _mix(t["field"], lift, 0.06),
        "FIELD_BORDER_HOVER": _mix(t["field_border"], accent, 0.45),
        "POPUP_BG": _rgba(card, 0.985 if not dark else 0.97),
        "ACCENT": accent,
        "ACCENT_HOVER": t["accent_hover"],
        "ACCENT_PRESSED": t["accent_pressed"],
        "ACCENT_SOFT": _rgba(accent, 0.12 if not dark else 0.18),
        "ACCENT_RING": _rgba(accent, 0.22),
        "ACCENT_DISABLED": _mix(accent, card, 0.62),
        "TEXT_PRIMARY": ink,
        "TEXT_SECONDARY": t["ink2"],
        "TEXT_MUTED": t["ink3"],
        "DANGER": "#E5484D" if not dark else "#FF6B70",
        "OK": "#3AA675" if not dark else "#4CD4A0",
        "SHADOW_RGB": t["shadow"],
        "SHADOW_ALPHA": 52 if not dark else 96,
        "IS_DARK": dark,
        # ---- 由核心色推导的辅助令牌（QSS 里那些原本写死的杂色）----
        "BTN_HOVER_BG": _mix(card, accent, 0.06),
        "BTN_HOVER_BORDER": _mix(t["card_border"], accent, 0.35),
        "BTN_PRESSED_BG": _mix(card, accent, 0.12),
        "ROW_HOVER_BG": _mix(card, accent, 0.05),
        "CHECK_BORDER": _mix(t["field_border"], ink, 0.25),
        "CHECK_BG": card,
        "MENU_BG": _mix(card, lift, 0.0) if not dark else _mix(card, lift, 0.04),
        "TOOLTIP_BG": _mix(card, lift, 0.0) if not dark else _mix(card, lift, 0.06),
        "SCROLL_HANDLE": _rgba(ink, 0.16 if not dark else 0.24),
        "SCROLL_HANDLE_HOVER": _rgba(ink, 0.30 if not dark else 0.42),
        "TAB_HOVER_BG": _rgba(ink, 0.05 if not dark else 0.10),
        "WINBTN_HOVER_BG": _rgba(ink, 0.07 if not dark else 0.12),
        "DIVIDER": _rgba(t["ink2"], 0.30),
        "SHELL_BORDER": _rgba("#FFFFFF", 0.9) if not dark else _rgba(lift, 0.08),
        # ---- 背景/横幅用 ----
        "HAS_PHOTO": t.get("photo", True),
        "BACKDROP_VEIL": t["veil"],
        "BACKDROP_VEIL_TOP": t["veil_top"],
        "BACKDROP_BLUR": t["blur"],
        "BACKDROP_FOCUS": t["bg_focus"],
        "HERO_INK": t["hero_ink"],
        "HERO_SUB_INK": t["hero_sub"],
        "THEME_DIR": key,
        "MOTION": t["motion"],
        "SINK": sink,
    }


def apply(key: str) -> str:
    """切换主题：重绑定本模块的全局令牌。返回实际生效的主题 key。"""
    global _ACTIVE
    if key not in _THEMES:
        key = DEFAULT_THEME
    _ACTIVE = key
    globals().update(_tokens(key))
    return key


# ---- 尺寸类常量与主题无关 ----
WINDOW_RADIUS = 14          # 窗体圆角（自绘外壳）
RADIUS = 14                 # 卡片
RADIUS_SM = 10              # 控件
# 输入框/下拉框内文字距控件左边缘的距离（12px padding + 1px 边框）：
# 控件下方的说明文字按这个值内缩，才会和控件里的文字左对齐
FIELD_TEXT_INSET = 13

FONT_FAMILY = '"Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC", "Segoe UI", sans-serif'

# 导入即先把默认主题的令牌绑上（模块级常量的名字由此产生）
apply(DEFAULT_THEME)


def asset_path(name: str) -> str:
    """资源文件路径，兼容 PyInstaller 冻结环境（_MEIPASS）。"""
    import sys

    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parent.parent.parent
    p = base / "assets" / name
    return str(p) if p.exists() else ""


def theme_asset(name: str, key: str = "") -> str:
    """当前主题目录下的资源；缺失返回空串（调用方自行降级）。"""
    return asset_path(f"themes/{key or _ACTIVE}/{name}")


_ARROW_SVG_TPL = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="6" viewBox="0 0 10 6">'
    '<path d="M1 1 L5 5 L9 1" fill="none" stroke="{color}" stroke-width="1.6" '
    'stroke-linecap="round" stroke-linejoin="round"/></svg>'
)


def _arrow_icon_url() -> str:
    """QSS 不支持 data: URI，落一个 SVG 文件供 url() 引用（路径用正斜杠）。

    箭头颜色跟着主题走，所以每套主题各落一个文件（深色主题下原来的灰绿箭头看不见）。
    """
    from ..config import CONFIG_DIR

    svg = _ARROW_SVG_TPL.format(color=TEXT_SECONDARY)
    path = Path(CONFIG_DIR) / "assets" / f"arrow-down-{_ACTIVE}.svg"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text(encoding="utf-8") != svg:
            path.write_text(svg, encoding="utf-8")
    except OSError:
        return ""
    return path.as_posix()


def app_qss() -> str:
    arrow = _arrow_icon_url()
    arrow_rule = f'image: url("{arrow}");' if arrow else "image: none;"
    # 有背景照片时，Shell 自己不画底色（照片层在它上面、内容层下面）
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
    border: 1px solid {SHELL_BORDER};
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
    background: {WINBTN_HOVER_BG};
    color: {TEXT_PRIMARY};
}}
QPushButton#WinBtnClose:hover {{
    background: {DANGER};
    color: white;
}}
/* ---- 输入类：比卡片更"凹"一层，聚焦时提亮 + 品牌色描边 ---- */
QPlainTextEdit, QTextEdit, QLineEdit {{
    background: {FIELD_BG};
    border: 1px solid {FIELD_BORDER};
    border-radius: {RADIUS_SM}px;
    padding: 9px 12px;
    font-size: 14px;
    selection-background-color: {ACCENT};
    selection-color: {"#0B1020" if IS_DARK else "white"};
}}
QLineEdit {{
    min-height: 22px;  /* 防止高分屏/全屏下输入框塌矮裁字 */
}}
QPlainTextEdit:focus, QTextEdit:focus, QLineEdit:focus {{
    background: {FIELD_BG_FOCUS};
    border: 1px solid {ACCENT};
}}
QPlainTextEdit:hover, QTextEdit:hover, QLineEdit:hover {{
    border-color: {FIELD_BORDER_HOVER};
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
    background: {BTN_HOVER_BG};
    border-color: {BTN_HOVER_BORDER};
}}
QPushButton:pressed {{
    background: {BTN_PRESSED_BG};
}}
QPushButton#Primary {{
    background: {ACCENT};
    color: {"#0B1020" if IS_DARK else "white"};
    border: 1px solid {ACCENT};
    font-weight: 600;
    padding: 8px 20px;
}}
QPushButton#Primary:hover {{
    background: {ACCENT_HOVER};
    border-color: {ACCENT_HOVER};
}}
QPushButton#Primary:pressed {{
    background: {ACCENT_PRESSED};
}}
QPushButton#Primary:disabled {{
    background: {ACCENT_DISABLED};
    border-color: {ACCENT_DISABLED};
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
    border-color: {FIELD_BORDER_HOVER};
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
    background: {MENU_BG};
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
    color: {"#0B1020" if IS_DARK else "white"};
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
    border: 1px solid {CHECK_BORDER};
    background: {CHECK_BG};
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
    background: {SCROLL_HANDLE};
    border-radius: 4px;
    min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{
    background: {SCROLL_HANDLE_HOVER};
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
    background: {TAB_HOVER_BG};
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
    background: {ROW_HOVER_BG};
}}
QLabel#HistMeta {{ color: {TEXT_MUTED}; font-size: 11px; }}
QLabel#HistSrc {{ color: {TEXT_SECONDARY}; font-size: 13px; }}
QLabel#HistRes {{ color: {TEXT_PRIMARY}; font-size: 14px; font-weight: 500; }}
/* ---- 主题卡（设置页外观选择器）---- */
QWidget#ThemeCard {{
    background: {CARD_BG};
    border: 1px solid {CARD_BORDER};
    border-radius: {RADIUS_SM}px;
}}
QWidget#ThemeCard:hover {{
    border-color: {ACCENT};
}}
QWidget#ThemeCardOn {{
    background: {ACCENT_SOFT};
    border: 2px solid {ACCENT};
    border-radius: {RADIUS_SM}px;
}}
QLabel#ThemeName {{ color: {TEXT_PRIMARY}; font-size: 12px; }}
QLabel#ThemeNameOn {{ color: {ACCENT}; font-size: 12px; font-weight: 600; }}
/* ---- 菜单（托盘菜单 / 弹窗复制菜单共用） ---- */
QMenu {{
    background: {MENU_BG};
    border: 1px solid {CARD_BORDER};
    border-radius: 8px;
    padding: 5px;
}}
QMenu::item {{
    padding: 6px 14px;
    border-radius: 7px;
    color: {TEXT_PRIMARY};
    font-size: 13px;
    background: transparent;
}}
QMenu::item:selected {{
    background: {ACCENT_SOFT};
    color: {ACCENT_HOVER};
}}
QMenu::item:disabled {{
    color: {TEXT_MUTED};
}}
QMenu::separator {{
    height: 1px;
    background: {CARD_BORDER};
    margin: 4px 8px;
}}
QToolTip {{
    background: {TOOLTIP_BG};
    color: {TEXT_PRIMARY};
    border: 1px solid {CARD_BORDER};
    border-radius: 7px;
    padding: 4px 8px;
}}
"""
