"""应用内更新。

更新源 = https://translate.ivyea.com/download/version.json（服务器 cron 从
GitHub Release 自动同步生成）。

流程（与主流桌面软件一致）：
  启动静默检查 / 设置页手动检查 -> 发现新版 -> 下载安装包(带进度)
  -> 写一个等待脚本(等本进程退出 -> 静默安装 -> 重启应用) -> 应用自己退出。
非安装版（macOS / 源码运行）无法覆盖安装，退化为打开官网下载页。
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple

import httpx
from PySide6.QtCore import QThread, Signal

from . import __version__
from .config import CONFIG_DIR

log = logging.getLogger(__name__)

_WINDOWS = sys.platform == "win32"

DEFAULT_FEED_URL = "https://translate.ivyea.com/download/version.json"


# ---------- 纯函数（可单测） ----------

def parse_version(s: str) -> Tuple[int, ...]:
    """"v0.3.1" / "0.3.1" -> (0, 3, 1)。解析失败抛 ValueError。"""
    s = (s or "").strip().lstrip("vV")
    parts = s.split(".")
    if not parts or not all(p.isdigit() for p in parts):
        raise ValueError(f"非法版本号: {s!r}")
    return tuple(int(p) for p in parts)


def is_newer(remote: str, local: str) -> bool:
    try:
        return parse_version(remote) > parse_version(local)
    except ValueError:
        return False


def normalize_feed(data: object) -> Optional[Dict]:
    """校验更新源结构；合法返回 dict，不合法返回 None。"""
    if not isinstance(data, dict):
        return None
    version = data.get("version")
    setup_url = data.get("setup_url")
    if not isinstance(version, str) or not isinstance(setup_url, str):
        return None
    if not setup_url.startswith("https://"):
        return None
    try:
        parse_version(version)
    except ValueError:
        return None
    return {
        "version": version.lstrip("vV"),
        "setup_url": setup_url,
        "notes": data.get("notes", "") if isinstance(data.get("notes"), str) else "",
        "page_url": data.get("page_url", "https://translate.ivyea.com/"),
    }


def _looks_installed(exe_path: Path, localappdata: str) -> bool:
    """纯逻辑（可单测）：判断 exe 是否属于 Inno 安装版。

    主判据：安装目录里有卸载器 unins*.exe（Inno 安装版必有），
    与安装位置无关，避免 LOCALAPPDATA 路径匹配 + resolve() 长路径前缀带来的误判。
    兜底：exe 位于 LOCALAPPDATA 下。
    """
    exe_dir = exe_path.parent
    try:
        if any(exe_dir.glob("unins*.exe")):
            return True
    except OSError:
        pass
    if localappdata:
        try:
            return Path(localappdata).resolve() in exe_path.parents
        except OSError:
            return False
    return False


def is_installed_copy() -> bool:
    """是否是安装版（PyInstaller 冻结 + Inno 安装目录）。安装版才能静默升级。"""
    if not getattr(sys, "frozen", False) or not _WINDOWS:
        return False
    return _looks_installed(Path(sys.executable).resolve(), os.environ.get("LOCALAPPDATA", ""))


# ---------- 线程 ----------

class UpdateChecker(QThread):
    update_available = Signal(dict)
    no_update = Signal()
    failed = Signal(str)

    def __init__(self, feed_url: str = DEFAULT_FEED_URL, parent=None):
        super().__init__(parent)
        self._url = feed_url

    def run(self):
        try:
            resp = httpx.get(self._url, timeout=15, follow_redirects=True)
            resp.raise_for_status()
            feed = normalize_feed(resp.json())
        except Exception as e:
            self.failed.emit(f"检查更新失败：{e.__class__.__name__}")
            log.info("检查更新失败：%s", e)
            return
        if feed is None:
            self.failed.emit("更新源格式异常")
            return
        if is_newer(feed["version"], __version__):
            log.info("发现新版本 %s（当前 %s）", feed["version"], __version__)
            self.update_available.emit(feed)
        else:
            self.no_update.emit()


class UpdateDownloader(QThread):
    progress = Signal(int)          # 0-100
    finished_ok = Signal(str)       # 安装包本地路径
    failed = Signal(str)

    def __init__(self, url: str, version: str, parent=None):
        super().__init__(parent)
        self._url = url
        self._version = version

    def run(self):
        dest_dir = CONFIG_DIR / "updates"
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"IvyeaTranslate-Setup-{self._version}.exe"
            tmp = dest.with_suffix(".part")
            with httpx.stream("GET", self._url, timeout=httpx.Timeout(600, connect=15),
                              follow_redirects=True) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length", 0))
                done = 0
                last_pct = -1
                with open(tmp, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=256 * 1024):
                        f.write(chunk)
                        done += len(chunk)
                        if total:
                            pct = int(done * 100 / total)
                            if pct != last_pct:
                                last_pct = pct
                                self.progress.emit(pct)
            if total and done < total:
                raise IOError(f"下载不完整 {done}/{total}")
            os.replace(tmp, dest)
            log.info("更新包下载完成：%s（%d 字节）", dest, done)
            self.finished_ok.emit(str(dest))
        except Exception as e:
            log.warning("更新包下载失败：%s", e)
            self.failed.emit(f"下载失败:{e.__class__.__name__}: {e}")


# ---------- 应用更新 ----------

_HELPER_BAT = """@echo off
rem Ivyea Translate self-update helper. Args: <pid> <setup.exe> <relaunch.exe>
powershell -NoProfile -Command "Wait-Process -Id %~1 -ErrorAction SilentlyContinue"
"%~2" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
start "" "%~3"
del "%~f0"
"""


def apply_update_and_quit(setup_path: str, quit_callback) -> None:
    """写等待脚本并启动：等本进程退出 -> 静默安装 -> 重启。随后触发应用退出。"""
    bat = Path(tempfile.gettempdir()) / "ivyea_translate_update.bat"
    with open(bat, "w", encoding="ascii") as f:  # 纯 ASCII 内容，路径经参数传递避免编码问题
        f.write(_HELPER_BAT)
    relaunch = sys.executable
    creation = 0
    if _WINDOWS:
        creation = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    subprocess.Popen(
        ["cmd.exe", "/c", str(bat), str(os.getpid()), setup_path, relaunch],
        creationflags=creation,
        close_fds=True,
    )
    log.info("更新脚本已启动，应用退出以完成升级")
    quit_callback()
