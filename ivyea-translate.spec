# PyInstaller 打包配置（在 Windows 机器上执行）：
#   pip install pyinstaller
#   pyinstaller ivyea-translate.spec
# 产物在 dist/IvyeaTranslate/ 下，运行 IvyeaTranslate.exe
import sys
from PyInstaller.utils.hooks import collect_data_files

# RapidOCR 的 onnx 模型和配置文件必须随包分发
datas = collect_data_files("rapidocr_onnxruntime")

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    datas=datas,
    hiddenimports=[
        "pynput.keyboard._win32",
        "pynput.mouse._win32",
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
