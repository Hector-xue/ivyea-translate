# PyInstaller 单文件便携版：产出一个独立 IvyeaTranslate.exe（免安装，双击即用）
# 在 Windows 上执行：pyinstaller ivyea-translate-portable.spec
# 说明：单文件版首次启动会解压到临时目录，比文件夹版慢几秒；体积约 200MB+
from PyInstaller.utils.hooks import collect_data_files

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
    a.binaries,
    a.datas,
    name="IvyeaTranslate",
    console=False,
    icon="assets/icon.ico",
    upx=False,
)
