"""本地 OCR：RapidOCR(onnxruntime) 封装 + 行框合并段落。

merge_lines 是纯函数，可单测：把 OCR 出的行（带包围框）按纵向间距聚成段落，
段内行用空格/直接拼接（按语言判断），段间用空行分隔。
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import List, Optional, Sequence


@dataclass
class OcrLine:
    text: str
    # 包围框：左上 x/y、宽、高（像素）
    x: float
    y: float
    w: float
    h: float


_CJK_RE = re.compile(r"[一-鿿぀-ヿ가-힯]")


def _joiner(prev: str, curr: str) -> str:
    """CJK 行间直接拼接；拉丁行间补空格；prev 以连字符结尾去连字符拼接。"""
    if prev.endswith("-") and not _CJK_RE.search(prev[-2:-1] or ""):
        return ""
    if _CJK_RE.search(prev[-1:]) or _CJK_RE.search(curr[:1]):
        return ""
    return " "


def merge_lines(lines: Sequence[OcrLine]) -> str:
    """按 y 排序，行距 > 0.8×行高视为新段落；段内智能拼接。"""
    valid = [ln for ln in lines if ln.text.strip()]
    if not valid:
        return ""
    ordered = sorted(valid, key=lambda ln: (ln.y, ln.x))
    paragraphs: List[List[str]] = [[ordered[0].text.strip()]]
    prev = ordered[0]
    for ln in ordered[1:]:
        gap = ln.y - (prev.y + prev.h)
        ref_h = max(min(prev.h, ln.h), 1.0)
        if gap > 0.8 * ref_h:
            paragraphs.append([ln.text.strip()])
        else:
            paragraphs[-1].append(ln.text.strip())
        prev = ln
    out_paras: List[str] = []
    for para in paragraphs:
        buf = para[0]
        for piece in para[1:]:
            join = _joiner(buf, piece)
            if join == "" and buf.endswith("-"):
                buf = buf[:-1]
            buf += join + piece
        out_paras.append(buf)
    return "\n\n".join(out_paras)


class OcrEngine:
    """RapidOCR 懒加载单例封装。首次加载慢（模型初始化），支持后台预热。"""

    def __init__(self):
        self._engine = None
        self._lock = threading.Lock()
        self._load_error: Optional[str] = None

    def warmup_async(self) -> None:
        threading.Thread(target=self._ensure_loaded, daemon=True).start()

    def _ensure_loaded(self):
        with self._lock:
            if self._engine is not None or self._load_error is not None:
                return self._engine
            try:
                from rapidocr_onnxruntime import RapidOCR

                self._engine = RapidOCR()
            except Exception as e:
                self._load_error = f"OCR 引擎加载失败：{e}"
            return self._engine

    def recognize(self, image_path: str) -> str:
        """识别图片文件，返回合并成段落的文本。失败抛 RuntimeError。"""
        engine = self._ensure_loaded()
        if engine is None:
            raise RuntimeError(self._load_error or "OCR 引擎不可用")
        result, _ = engine(image_path)
        if not result:
            return ""
        lines: List[OcrLine] = []
        for item in result:
            # RapidOCR 返回 [四点框, 文本, 置信度]
            box, text = item[0], item[1]
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            lines.append(
                OcrLine(
                    text=str(text),
                    x=float(min(xs)),
                    y=float(min(ys)),
                    w=float(max(xs) - min(xs)),
                    h=float(max(ys) - min(ys)),
                )
            )
        return merge_lines(lines)


# 全局单例
ocr_engine = OcrEngine()
