# PyInstaller 打包配置（在 Windows 机器上执行）：
#   pip install pyinstaller
#   pyinstaller ivyea-translate.spec
# 产物在 dist/IvyeaTranslate/ 下，运行 IvyeaTranslate.exe
import sys
from PyInstaller.utils.hooks import collect_data_files

# RapidOCR 的 onnx 模型和配置文件必须随包分发；assets 是品牌 logo/图标
datas = collect_data_files("rapidocr_onnxruntime") + [("assets", "assets")]

# 系统 OCR（pywinrt）：winrt 是跨多个发行包的命名空间包，扩展模块与 msvcp140.dll
# 靠静态分析收不全，显式把它的动态库都带上；没装（非 Windows）就跳过
binaries = []
try:
    from PyInstaller.utils.hooks import collect_dynamic_libs

    binaries = collect_dynamic_libs("winrt")
    datas += collect_data_files("winrt", include_py_files=False)
except Exception:
    pass

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "pynput.keyboard._win32",
        "pynput.mouse._win32",
        # 系统 OCR：pywinrt 是运行时按需 import 的，PyInstaller 静态分析看不到
        "winrt.windows.media.ocr",
        "winrt.windows.graphics.imaging",
        "winrt.windows.storage.streams",
        "winrt.windows.globalization",
        "winrt.windows.foundation",
        "winrt.windows.foundation.collections",
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
    console=False,          # 无控制台窗口
    icon="assets/icon.ico",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="IvyeaTranslate",
)
