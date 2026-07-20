# PyInstaller 打包配置（在 macOS 上执行）：
#   pip install pyinstaller
#   pyinstaller ivyea-translate-mac.spec
# 产物 dist/IvyeaTranslate.app（CI 再用 hdiutil 打成 dmg）
#
# 未签名分发：用户首次打开会被 Gatekeeper 拦（"无法验证开发者"），
# 需右键→打开，或 xattr -dr com.apple.quarantine /Applications/IvyeaTranslate.app
import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

# 版本号只在 ivyea_translate/__init__.py 维护一处；这里正则取，避免 import 整个包
_VERSION = re.search(
    r'__version__\s*=\s*"([^"]+)"',
    Path("ivyea_translate/__init__.py").read_text(encoding="utf-8"),
).group(1)

# RapidOCR 的 onnx 模型和配置文件必须随包分发；assets 是品牌 logo/图标
datas = collect_data_files("rapidocr_onnxruntime") + [("assets", "assets")]

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    datas=datas,
    hiddenimports=[
        # 全局热键在 macOS 走 pynput（Windows 走原生 RegisterHotKey），
        # 后端模块是运行时按平台 import 的，PyInstaller 静态分析看不到
        "pynput.keyboard._darwin",
        "pynput.mouse._darwin",
    ],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="IvyeaTranslate",
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="IvyeaTranslate",
)
app = BUNDLE(
    coll,
    name="IvyeaTranslate.app",
    # CI 用 sips+iconutil 从 icon.png 生成；本地没生成时不因缺图标而构建失败
    icon="assets/icon.icns" if Path("assets/icon.icns").exists() else None,
    bundle_identifier="com.ivyea.translate",
    info_plist={
        "CFBundleName": "Ivyea Translate",
        "CFBundleDisplayName": "Ivyea Translate",
        "CFBundleShortVersionString": _VERSION,
        "CFBundleVersion": _VERSION,
        "NSHighResolutionCapable": True,
        # 截图翻译要抓屏、划词要读剪贴板/全局热键：系统会弹权限申请，
        # 说明文案缺失会导致部分系统直接拒绝而不是弹窗
        "NSAppleEventsUsageDescription": "用于全局快捷键与划词翻译。",
        "NSCameraUsageDescription": "不使用摄像头。",
    },
)
