"""配置读写：~/.ivyea-translate/config.json

所有默认值集中在 DEFAULT_CONFIG；load 时深合并，缺失键自动补默认，
保证旧版本配置文件升级后不缺字段。
"""
from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Tuple

CONFIG_DIR = Path(os.environ.get("IVYEA_TRANSLATE_HOME", str(Path.home() / ".ivyea-translate")))
CONFIG_PATH = CONFIG_DIR / "config.json"

# OpenAI 兼容接口预设：选中后填 base_url，model 给个常用默认，api_key 用户自己填
PROVIDER_PRESETS: Dict[str, Dict[str, str]] = {
    "deepseek": {
        "label": "DeepSeek 官方",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "deepseek/deepseek-chat",
    },
    "siliconflow": {
        "label": "硅基流动",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "deepseek-ai/DeepSeek-V3",
    },
    "custom": {
        "label": "自定义",
        "base_url": "",
        "model": "",
    },
}

# 目标语言：code -> 中文名（prompt 里用英文名，见 translator.LANGUAGE_NAMES）
LANGUAGES: List[Tuple[str, str]] = [
    ("zh-CN", "简体中文"),
    ("zh-TW", "繁体中文"),
    ("en", "英语"),
    ("ja", "日语"),
    ("ko", "韩语"),
    ("fr", "法语"),
    ("de", "德语"),
    ("es", "西班牙语"),
    ("ru", "俄语"),
    ("pt", "葡萄牙语"),
    ("it", "意大利语"),
    ("ar", "阿拉伯语"),
    ("vi", "越南语"),
    ("th", "泰语"),
]

# 翻译风格：code -> 中文名。american/british 仅当目标语言为英语时生效（UI 会做联动提示）
STYLES: List[Tuple[str, str]] = [
    ("general", "通用"),
    ("american", "美式英语"),
    ("british", "英式英语"),
    ("formal", "正式"),
    ("casual", "口语"),
    ("academic", "学术"),
    ("concise", "简洁"),
]

DEFAULT_CONFIG: Dict[str, Any] = {
    "provider": {
        "preset": "deepseek",
        "base_url": PROVIDER_PRESETS["deepseek"]["base_url"],
        "api_key": "",
        "model": PROVIDER_PRESETS["deepseek"]["model"],
        "temperature": 0.3,
        "timeout": 60,
    },
    "translate": {
        "target_language": "zh-CN",
        "style": "general",
    },
    "email": {
        "target_language": "en",
        "tone": "business",
    },
    "hotkeys": {
        # pynput GlobalHotKeys 语法
        "select_translate": "<ctrl>+<alt>+t",
        "screenshot_translate": "<ctrl>+<alt>+s",
        "show_main_window": "<ctrl>+<alt>+i",
    },
    "clipboard_watch": {
        "enabled": False,
        "max_chars": 3000,
    },
    "selection_bubble": {
        # 划词后光标旁弹出小图标，点击即翻译（仅 Windows）
        "enabled": True,
    },
    "ui": {
        "popup_width": 520,
        "history_limit": 100,
    },
    "update": {
        "auto_check": True,
        "feed_url": "https://translate.ivyea.com/download/version.json",
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """override 覆盖 base，嵌套 dict 递归合并；返回新 dict。"""
    out = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = deepcopy(value)
    return out


class Config:
    """线程安全的配置对象。get/set 走点号路径，save 原子写。"""

    def __init__(self, path: Path = CONFIG_PATH):
        self._path = Path(path)
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = deepcopy(DEFAULT_CONFIG)
        self.load()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> None:
        with self._lock:
            if self._path.exists():
                try:
                    with open(self._path, "r", encoding="utf-8") as f:
                        user_data = json.load(f)
                    if isinstance(user_data, dict):
                        self._data = _deep_merge(DEFAULT_CONFIG, user_data)
                        self._migrate()
                except (json.JSONDecodeError, OSError):
                    # 配置损坏时不覆盖用户文件，用默认值继续跑
                    self._data = deepcopy(DEFAULT_CONFIG)

    def _migrate(self) -> None:
        """老配置的默认值升级（用户自定义过的值不动）。"""
        ui = self._data.get("ui", {})
        # v0.1.x 默认弹窗宽 420 偏小；等于旧默认值视为未自定义，升到新默认
        if ui.get("popup_width") == 420:
            ui["popup_width"] = DEFAULT_CONFIG["ui"]["popup_width"]

    def save(self) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)

    def get(self, dotted_key: str, default: Any = None) -> Any:
        with self._lock:
            node: Any = self._data
            for part in dotted_key.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return deepcopy(node)

    def set(self, dotted_key: str, value: Any) -> None:
        with self._lock:
            parts = dotted_key.split(".")
            node = self._data
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value

    def as_dict(self) -> Dict[str, Any]:
        with self._lock:
            return deepcopy(self._data)
