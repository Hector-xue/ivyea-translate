"""本地 OCR：RapidOCR(onnxruntime) 封装 + 行框合并段落。

merge_lines 是纯函数，可单测：把 OCR 出的行（带包围框）按纵向间距聚成段落，
段内行用空格/直接拼接（按语言判断），段间用空行分隔。
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import List, Optional, Sequence

log = logging.getLogger(__name__)

# 屏幕截图文字通常偏小，放大后识别率显著更高
UPSCALE_THRESHOLD = 1600  # 长边小于此值时放大
UPSCALE_FACTOR = 2


def compute_upscale(width: int, height: int) -> int:
    """纯函数：给定图片尺寸返回放大倍数（1 = 不放大）。"""
    if width <= 0 or height <= 0:
        return 1
    return UPSCALE_FACTOR if max(width, height) < UPSCALE_THRESHOLD else 1


@dataclass
class OcrLine:
    text: str
    # 包围框：左上 x/y、宽、高（像素）
    x: float
    y: float
    w: float
    h: float


@dataclass
class OcrBlock:
    """一个段落：合并后的文本 + 该段所有行的并集包围框（原图物理像素）。"""

    text: str
    x: float
    y: float
    w: float
    h: float
    line_h: float = 0.0   # 平均行高，原位模式据此定初始字号
    lines: int = 1


_CJK_RE = re.compile(r"[一-鿿぀-ヿ가-힯]")


def _joiner(prev: str, curr: str) -> str:
    """CJK 行间直接拼接；拉丁行间补空格；prev 以连字符结尾去连字符拼接。"""
    if prev.endswith("-") and not _CJK_RE.search(prev[-2:-1] or ""):
        return ""
    if _CJK_RE.search(prev[-1:]) or _CJK_RE.search(curr[:1]):
        return ""
    return " "


def _join_para(texts: Sequence[str]) -> str:
    buf = texts[0]
    for piece in texts[1:]:
        join = _joiner(buf, piece)
        if join == "" and buf.endswith("-"):
            buf = buf[:-1]
        buf += join + piece
    return buf


def group_lines(lines: Sequence[OcrLine]) -> List[OcrBlock]:
    """按 y 排序，行距 > 0.8×行高视为新段落；返回段落文本 + 该段的并集包围框。

    纯函数。原位翻译要把译文贴回每段原来的位置，所以段落必须带框；
    merge_lines 就是本函数的"只要文本"视图。
    """
    valid = [ln for ln in lines if ln.text.strip()]
    if not valid:
        return []
    ordered = sorted(valid, key=lambda ln: (ln.y, ln.x))
    groups: List[List[OcrLine]] = [[ordered[0]]]
    prev = ordered[0]
    for ln in ordered[1:]:
        gap = ln.y - (prev.y + prev.h)
        ref_h = max(min(prev.h, ln.h), 1.0)
        if gap > 0.8 * ref_h:
            groups.append([ln])
        else:
            groups[-1].append(ln)
        prev = ln
    blocks: List[OcrBlock] = []
    for group in groups:
        x0 = min(ln.x for ln in group)
        y0 = min(ln.y for ln in group)
        x1 = max(ln.x + ln.w for ln in group)
        y1 = max(ln.y + ln.h for ln in group)
        blocks.append(
            OcrBlock(
                text=_join_para([ln.text.strip() for ln in group]),
                x=x0, y=y0, w=x1 - x0, h=y1 - y0,
                line_h=sum(ln.h for ln in group) / len(group),
                lines=len(group),
            )
        )
    return blocks


def merge_lines(lines: Sequence[OcrLine]) -> str:
    """段落文本，段间空行分隔（原位模式之外的老链路仍用它）。"""
    return "\n\n".join(b.text for b in group_lines(lines))


def merge_near_blocks(blocks: Sequence[OcrBlock], gap_factor: float = 1.8) -> List[OcrBlock]:
    """把纵向挨得近的段落合并成一块（原位翻译用，纯函数）。

    group_lines 的阈值（0.8×行高）是给"拼成一段文字"用的，偏碎；原位模式要把
    译文贴回屏幕，碎块会变成一堆小卡片，既难看又更容易挤不下。这里用更宽松的
    间距把视觉上属于同一段的块并起来，横向不重叠的（多栏排版）不合并。
    """
    ordered = sorted(blocks, key=lambda b: (b.y, b.x))
    out: List[OcrBlock] = []
    for block in ordered:
        if out:
            prev = out[-1]
            gap = block.y - (prev.y + prev.h)
            ref_h = max(min(prev.line_h or prev.h, block.line_h or block.h), 1.0)
            overlap = min(prev.x + prev.w, block.x + block.w) - max(prev.x, block.x)
            if gap <= gap_factor * ref_h and overlap > 0.3 * min(prev.w, block.w):
                out[-1] = bounding_block([prev, block])
                continue
        out.append(block)
    return out


def bounding_block(blocks: Sequence[OcrBlock]) -> OcrBlock:
    """把多个段落并成一个大框（原位翻译对不上段数时的降级目标）。"""
    x0 = min(b.x for b in blocks)
    y0 = min(b.y for b in blocks)
    x1 = max(b.x + b.w for b in blocks)
    y1 = max(b.y + b.h for b in blocks)
    return OcrBlock(
        text="\n\n".join(b.text for b in blocks),
        x=x0, y=y0, w=x1 - x0, h=y1 - y0,
        line_h=sum(b.line_h for b in blocks) / len(blocks),
        lines=sum(b.lines for b in blocks),
    )


def scale_blocks(blocks: Sequence[OcrBlock], scale: int) -> List[OcrBlock]:
    """把放大图上的坐标折回原图物理像素（scale=1 时原样返回）。"""
    if scale <= 1:
        return list(blocks)
    return [
        OcrBlock(text=b.text, x=b.x / scale, y=b.y / scale,
                 w=b.w / scale, h=b.h / scale,
                 line_h=b.line_h / scale, lines=b.lines)
        for b in blocks
    ]


def qimage_to_rgb(qimage) -> "object":
    """QImage -> RGB ndarray（拷贝一份，安全跨线程；行按 bytesPerLine 对齐）。

    截图翻译曾把截图编码成 PNG 落盘、OCR 线程再读回解码——一来一回
    50-200ms 纯浪费。现在内存直通。
    """
    import numpy as np
    from PySide6.QtGui import QImage

    img = qimage.convertToFormat(QImage.Format_RGB888)
    h, w, bpl = img.height(), img.width(), img.bytesPerLine()
    buf = np.frombuffer(img.constBits(), dtype=np.uint8, count=h * bpl).reshape(h, bpl)
    return buf[:, : w * 3].reshape(h, w, 3).copy()


def recognize_blocks_from_result(result: Sequence, scale: int = 1) -> List[OcrBlock]:
    """把 RapidOCR 的原始返回解析成段落块（纯函数，可单测）。

    RapidOCR 每项是 [四点框, 文本, 置信度]；坐标按放大倍数折回原图尺度。
    """
    if not result:
        return []
    lines: List[OcrLine] = []
    for item in result:
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
    return scale_blocks(group_lines(lines), scale)


class OcrEngine:
    """RapidOCR 懒加载单例封装。首次加载慢（模型初始化），支持后台预热。"""

    def __init__(self):
        self._engine = None
        self._lock = threading.Lock()
        self._load_error: Optional[str] = None

    def warmup_async(self) -> None:
        """后台加载模型并跑一次真实推理：ONNX 首次推理有编译/分配开销，
        不预热的话用户第一次截图要多等 2-4 秒。"""

        def warm():
            engine = self._ensure_loaded()
            if engine is None:
                return
            try:
                import numpy as np

                dummy = np.full((48, 160, 3), 255, dtype=np.uint8)
                t0 = time.monotonic()
                engine(dummy)
                log.info("OCR 预热完成，耗时 %.1fs", time.monotonic() - t0)
            except Exception as e:
                log.warning("OCR 预热失败：%s", e)

        threading.Thread(target=warm, daemon=True).start()

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
        return "\n\n".join(b.text for b in self.recognize_blocks(image_path))

    def recognize_blocks(self, image_path: str) -> List[OcrBlock]:
        """识别图片文件（老接口，测试/兼容用），返回带包围框的段落列表。"""
        from PIL import Image

        return self._recognize_pil(Image.open(image_path).convert("RGB"))

    def recognize_blocks_array(self, arr) -> List[OcrBlock]:
        """识别 RGB ndarray（截图内存直通，不落盘），坐标 = 原图物理像素。"""
        from PIL import Image

        return self._recognize_pil(Image.fromarray(arr))

    def _recognize_pil(self, img) -> List[OcrBlock]:
        """小图先放大再识别：屏幕字号小，直接喂模型漏字/错字明显。
        放大只是识别手段，坐标必须折回原图尺度，否则原位翻译会把译文贴到
        两倍远的地方——这是本功能最容易踩的坑。插值用 BICUBIC：对识别精度
        与 LANCZOS 无差，但大图快 2-3 倍。
        """
        engine = self._ensure_loaded()
        if engine is None:
            raise RuntimeError(self._load_error or "OCR 引擎不可用")
        t0 = time.monotonic()
        import numpy as np
        from PIL import Image

        scale = compute_upscale(*img.size)
        if scale > 1:
            img = img.resize((img.width * scale, img.height * scale), Image.BICUBIC)
        result, _ = engine(np.array(img))
        log.info("OCR 完成：%s 行，放大×%d，耗时 %.1fs",
                 len(result) if result else 0, scale, time.monotonic() - t0)
        return recognize_blocks_from_result(result, scale)


# 全局单例
ocr_engine = OcrEngine()
