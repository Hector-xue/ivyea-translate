"""PyInstaller 打包入口。

不能直接用 ivyea_translate/__main__.py：它里面是相对导入（from .app import main），
PyInstaller 把入口当顶层脚本分析时无法解析相对导入，会导致 PySide6/RapidOCR
整个依赖树都不被收集，打出只有 20 多 MB 的空壳 exe。
"""
import sys

from ivyea_translate.app import main

if __name__ == "__main__":
    sys.exit(main())
