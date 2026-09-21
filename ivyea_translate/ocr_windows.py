"""Windows 系统 OCR（Windows.Media.Ocr）：微信截图识别那种"点完就出"的速度来源。

微信桌面端截图识别为什么几乎瞬时：它用的是自家 C++ 引擎（WeChatOCR.exe），模型
极小、常驻热着、对屏幕文字专门调过；我们走的 RapidOCR（PP-OCRv4 / onnxruntime，
Python 调度）在用户的无 GPU 机器上一次检测就要 2 秒多。Windows 10/11 自带的
Windows.Media.Ocr 和微信那套一个量级：系统级、小模型、几十到几百毫秒出结果，不用
下载任何东西（中文 Windows 自带 zh-CN 语言包）。代价是小字/花哨排版的精度不如
RapidOCR，所以做成可插拔：ocr.engine = auto（系统 OCR 优先，失败回退）/ windows / rapid。

坐标：返回的行框在**输入图像**的像素坐标系里（调用方自己处理放大倍数）。
中文行：系统 OCR 会在汉字之间塞空格（按"词"切），这里把 CJK 之间的空格去掉。

只在 Windows 上可用；其余平台 available() 恒为 False。pywinrt 的包
（winrt-Windows.Media.Ocr 等）装不上/导入失败也一律 False，调用方回退 RapidOCR。
"""
from __future__ import annotations

import logging
import re
import sys
import threading
from typing import List, Optional, Sequence

log = logging.getLogger(__name__)

_WINDOWS = sys.platform == "win32"
_lock = threading.Lock()
_engine = None
_engine_error: Optional[str] = None
_engine_lang = ""

_CJK = r"[一-鿿㐀-䶿぀-ヿ가-힯　-〿＀-￯]"
_CJK_SPACE_RE = re.compile(rf"(?<={_CJK})\s+(?={_CJK})")

# 目标语言 -> 优先尝试的识别语言标签（用户机器上未必都装了）
LANG_TAGS = {
    "zh-CN": ["zh-Hans-CN", "zh-Hans"],
    "zh-TW": ["zh-Hant-TW", "zh-Hant"],
    "en": ["en-US", "en-GB", "en"],
    "ja": ["ja", "ja-JP"],
    "ko": ["ko", "ko-KR"],
}


def collapse_cjk_spaces(text: str) -> str:
    """纯函数：去掉 CJK 字符之间的空格（系统 OCR 按词切时塞进去的）。"""
    return _CJK_SPACE_RE.sub("", text)


def line_box_from_words(words: Sequence[tuple]) -> tuple:
    """纯函数：词框 (x, y, w, h) 列表 -> 行的并集框 (x, y, w, h)。"""
    x0 = min(w[0] for w in words)
    y0 = min(w[1] for w in words)
    x1 = max(w[0] + w[2] for w in words)
    y1 = max(w[1] + w[3] for w in words)
    return (x0, y0, x1 - x0, y1 - y0)


def _create_engine(prefer_lang: str = ""):
    """按用户语言创建引擎（优先目标语言的识别包，没有就用系统档案里的语言）。"""
    from winrt.windows.globalization import Language
    from winrt.windows.media.ocr import OcrEngine

    tags = LANG_TAGS.get(prefer_lang, [])
    for tag in tags:
        try:
            lang = Language(tag)
            if OcrEngine.is_language_supported(lang):
                eng = OcrEngine.try_create_from_language(lang)
                if eng is not None:
                    return eng, tag
        except Exception:
            continue
    eng = OcrEngine.try_create_from_user_profile_languages()
    if eng is None:
        raise RuntimeError("系统 OCR 不可用：没有已安装的 OCR 语言包（设置 → 时间和语言 → 语言 → 可选功能）")
    try:
        tag = eng.recognizer_language.language_tag
    except Exception:
        tag = "?"
    return eng, tag


def available(prefer_lang: str = "") -> bool:
    """系统 OCR 能不能用（首次调用会真的建一次引擎，结果缓存）。"""
    if not _WINDOWS:
        return False
    return _ensure_engine(prefer_lang) is not None


def _init_apartment() -> None:
    """WinRT 调用前把当前线程放进 MTA（OCR 在工作线程里跑；主线程 Qt 已是 STA，不动它）。
    pywinrt 3.x 不再自动初始化；重复初始化/已在别的公寓类型里只会返回错误，忽略即可。"""
    try:
        from winrt.runtime import ApartmentType, init_apartment

        init_apartment(ApartmentType.MULTI_THREADED)
    except Exception:
        pass


def _ensure_engine(prefer_lang: str = ""):
    global _engine, _engine_error, _engine_lang
    with _lock:
        if _engine is not None or _engine_error is not None:
            return _engine
        try:
            _init_apartment()
            _engine, _engine_lang = _create_engine(prefer_lang)
            log.info("系统 OCR 就绪：识别语言 %s", _engine_lang)
        except Exception as e:
            _engine_error = f"{e.__class__.__name__}: {e}"
            log.info("系统 OCR 不可用，使用 RapidOCR：%s", _engine_error)
        return _engine


def error_reason() -> str:
    return _engine_error or ""


def recognize_lines(arr, prefer_lang: str = "") -> List["OcrLineT"]:
    """RGB ndarray -> 行列表（ocr.OcrLine），坐标为输入图像像素。抛异常给调用方回退。"""
    import numpy as np

    from .ocr import OcrLine

    engine = _ensure_engine(prefer_lang)
    if engine is None:
        raise RuntimeError(_engine_error or "系统 OCR 不可用")
    _init_apartment()   # 每个调用线程都要进公寓（QThread 池里的线程各不相同）
    from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine

    h, w = arr.shape[:2]
    limit = int(OcrEngine.max_image_dimension)
    if max(h, w) > limit:
        raise RuntimeError(f"图片超过系统 OCR 上限 {limit}px")
    # RGB -> BGRA（系统 OCR 只认 BGRA8/Gray8）
    bgra = np.empty((h, w, 4), dtype=np.uint8)
    bgra[..., 0] = arr[..., 2]
    bgra[..., 1] = arr[..., 1]
    bgra[..., 2] = arr[..., 0]
    bgra[..., 3] = 255
    bitmap = SoftwareBitmap.create_copy_with_alpha_from_buffer(
        bgra.tobytes(), BitmapPixelFormat.BGRA8, w, h, BitmapAlphaMode.IGNORE)
    try:
        result = engine.recognize_async(bitmap).get()
    finally:
        try:
            bitmap.close()
        except Exception:
            pass
    lines: List[OcrLine] = []
    for line in result.lines:
        words = []
        for word in line.words:
            r = word.bounding_rect
            words.append((float(r.x), float(r.y), float(r.width), float(r.height)))
        if not words:
            continue
        x, y, bw, bh = line_box_from_words(words)
        text = collapse_cjk_spaces(line.text or "").strip()
        if text:
            lines.append(OcrLine(text=text, x=x, y=y, w=bw, h=bh))
    return lines


OcrLineT = "OcrLine"
