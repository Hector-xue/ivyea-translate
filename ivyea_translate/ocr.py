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
from typing import Callable, List, Optional, Sequence

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


def group_line_indices(lines: Sequence[OcrLine], gap_factor: float = 0.8) -> List[List[int]]:
    """按 y 排序，行距 > gap_factor×行高视为新段落；返回每段包含的行下标（阅读序）。

    纯函数、只看几何不看文字：流水线要在识别文字之前就把版面分好段，
    这样第一段识别完就能先送去翻译，不必等整图识完。
    """
    order = sorted(range(len(lines)), key=lambda i: (lines[i].y, lines[i].x))
    if not order:
        return []
    groups: List[List[int]] = [[order[0]]]
    prev = lines[order[0]]
    for i in order[1:]:
        ln = lines[i]
        gap = ln.y - (prev.y + prev.h)
        ref_h = max(min(prev.h, ln.h), 1.0)
        if gap > gap_factor * ref_h:
            groups.append([i])
        else:
            groups[-1].append(i)
        prev = ln
    return groups


def block_from_lines(group: Sequence[OcrLine]) -> OcrBlock:
    """把一组行（同一段）拼成段落块：文本按语言规则拼接，框取并集。"""
    x0 = min(ln.x for ln in group)
    y0 = min(ln.y for ln in group)
    x1 = max(ln.x + ln.w for ln in group)
    y1 = max(ln.y + ln.h for ln in group)
    texts = [ln.text.strip() for ln in group if ln.text.strip()]
    return OcrBlock(
        text=_join_para(texts) if texts else "",
        x=x0, y=y0, w=x1 - x0, h=y1 - y0,
        line_h=sum(ln.h for ln in group) / len(group),
        lines=len(group),
    )


def group_lines(lines: Sequence[OcrLine]) -> List[OcrBlock]:
    """按 y 排序，行距 > 0.8×行高视为新段落；返回段落文本 + 该段的并集包围框。

    纯函数。原位翻译要把译文贴回每段原来的位置，所以段落必须带框；
    merge_lines 就是本函数的"只要文本"视图。
    """
    valid = [ln for ln in lines if ln.text.strip()]
    if not valid:
        return []
    return [block_from_lines([valid[i] for i in g]) for g in group_line_indices(valid)]


def merge_lines(lines: Sequence[OcrLine]) -> str:
    """段落文本，段间空行分隔（原位模式之外的老链路仍用它）。"""
    return "\n\n".join(b.text for b in group_lines(lines))


def near_group_indices(blocks: Sequence[OcrBlock], gap_factor: float = 1.8) -> List[List[int]]:
    """把纵向挨得近的段落归到同一组，返回每组包含的块下标（阅读序）。纯函数。

    只看几何：流水线在识别文字前就要定下原位卡片的分组。
    """
    order = sorted(range(len(blocks)), key=lambda i: (blocks[i].y, blocks[i].x))
    groups: List[List[int]] = []
    merged: List[OcrBlock] = []   # 每组当前的并集框，用来和下一个块比间距
    for i in order:
        block = blocks[i]
        if merged:
            prev = merged[-1]
            gap = block.y - (prev.y + prev.h)
            ref_h = max(min(prev.line_h or prev.h, block.line_h or block.h), 1.0)
            overlap = min(prev.x + prev.w, block.x + block.w) - max(prev.x, block.x)
            if gap <= gap_factor * ref_h and overlap > 0.3 * min(prev.w, block.w):
                groups[-1].append(i)
                merged[-1] = bounding_block([prev, block])
                continue
        groups.append([i])
        merged.append(block)
    return groups


def merge_near_blocks(blocks: Sequence[OcrBlock], gap_factor: float = 1.8) -> List[OcrBlock]:
    """把纵向挨得近的段落合并成一块（原位翻译用，纯函数）。

    group_lines 的阈值（0.8×行高）是给"拼成一段文字"用的，偏碎；原位模式要把
    译文贴回屏幕，碎块会变成一堆小卡片，既难看又更容易挤不下。这里用更宽松的
    间距把视觉上属于同一段的块并起来，横向不重叠的（多栏排版）不合并。
    """
    return [bounding_block([blocks[i] for i in g])
            for g in near_group_indices(blocks, gap_factor)]


def plan_paragraphs(lines: Sequence[OcrLine], near_gap: Optional[float] = None) -> List[List[List[int]]]:
    """识别前的版面规划（纯函数）：段落 → 子段 → 行下标。

    弹窗模式 near_gap=None：每段就是 group_line_indices 的一组。
    原位模式 near_gap=1.8：再把挨得近的段并成一张卡片（子段之间用空行分隔），
    与老链路 merge_near_blocks(group_lines(...)) 的结果一致，只是不需要先有文字。
    """
    groups = group_line_indices(lines)
    if near_gap is None:
        return [[g] for g in groups]
    # 用几何占位块跑一遍近邻归组（文字此时还没有，block_from_lines 给空文本）
    stubs = [block_from_lines([lines[i] for i in g]) for g in groups]
    return [[groups[j] for j in near] for near in near_group_indices(stubs, near_gap)]


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


def lines_from_boxes(boxes: Sequence) -> List[OcrLine]:
    """RapidOCR 仅检测（use_rec=False）返回的四点框 -> 空文本的行（纯函数）。"""
    lines: List[OcrLine] = []
    for box in boxes:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        lines.append(OcrLine(text="", x=float(min(xs)), y=float(min(ys)),
                             w=float(max(xs) - min(xs)), h=float(max(ys) - min(ys))))
    return lines


def _assemble_paragraph(subgroups: Sequence[Sequence[OcrLine]]) -> OcrBlock:
    """把一个段落（若干子段，每子段若干行）装配成块：子段间空行分隔，框取并集。"""
    subs = [block_from_lines(g) for g in subgroups if g]
    if len(subs) == 1:
        return subs[0]
    merged = bounding_block(subs)
    merged.text = "\n\n".join(b.text for b in subs if b.text)
    return merged


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

    # ---- 流水线：先检测版面，逐段识别、逐段回调 ----

    def recognize_streaming(self, arr, on_layout: Callable[[List[OcrBlock]], None],
                            on_block: Callable[[int, OcrBlock], None],
                            near_gap: Optional[float] = None,
                            should_abort: Optional[Callable[[], bool]] = None,
                            trace=None) -> int:
        """检测一次、按段识别、每识完一段回调一次。返回段落总数。

        整图识别是"检测 + 全部行识别"串行完才出第一个字；识别占 OCR 总时间六成
        以上（本机实测 7 行 1.6s 里识别 1.0s），而翻译又要等它。改成检测完先把
        版面（每段的框）交给界面占位（on_layout），然后按阅读序逐段识别、逐段交出
        （on_block），第一段识完就能送去翻译——OCR 与翻译重叠，首段译文提前出现。

        识别走 RapidOCR 公开接口逐行调用（use_det=False），不用内部批量接口：本机
        实测逐行 643ms、批量 1009ms——批量要把一批裁片补齐到最宽那张，白算一大片。
        坐标一律折回原图物理像素（放大只是识别手段）。
        """
        engine = self._ensure_loaded()
        if engine is None:
            raise RuntimeError(self._load_error or "OCR 引擎不可用")
        import numpy as np
        from PIL import Image

        img = Image.fromarray(arr)
        scale = compute_upscale(*img.size)
        if scale > 1:
            img = img.resize((img.width * scale, img.height * scale), Image.BICUBIC)
        big = np.array(img)
        t0 = time.monotonic()
        boxes, _ = engine(big, use_det=True, use_cls=False, use_rec=False)
        lines = lines_from_boxes(boxes or [])
        if trace is not None:
            trace.mark("检测")
        log.info("OCR 检测：%d 行，放大×%d，耗时 %.2fs", len(lines), scale, time.monotonic() - t0)
        plan = plan_paragraphs(lines, near_gap)
        # 版面占位：只有框没有字，界面据此先摆好位置
        layout = [
            _assemble_paragraph([[lines[i] for i in sub] for sub in para])
            for para in plan
        ]
        on_layout(scale_blocks(layout, scale))
        for idx, para in enumerate(plan):
            if should_abort is not None and should_abort():
                break
            for sub in para:
                for i in sub:
                    ln = lines[i]
                    ln.text = self._recognize_line(engine, big, ln)
            block = _assemble_paragraph([[lines[i] for i in sub] for sub in para])
            block = scale_blocks([block], scale)[0]
            if trace is not None:
                trace.mark("首段识别")
            on_block(idx, block)
        log.info("OCR 完成：%d 段，总耗时 %.2fs", len(plan), time.monotonic() - t0)
        return len(plan)

    @staticmethod
    def _recognize_line(engine, big, ln: OcrLine, pad: int = 2, min_score: float = 0.5) -> str:
        """识别一行：从放大图上裁包围框（留 2px 边）喂识别模型。屏幕文字不旋转，
        轴对齐裁剪足够；置信度低于阈值的行丢弃（与整图链路的 text_score 一致）。"""
        h, w = big.shape[:2]
        x0 = max(int(ln.x) - pad, 0)
        y0 = max(int(ln.y) - pad, 0)
        x1 = min(int(ln.x + ln.w) + pad, w)
        y1 = min(int(ln.y + ln.h) + pad, h)
        if x1 - x0 < 2 or y1 - y0 < 2:
            return ""
        result, _ = engine(big[y0:y1, x0:x1], use_det=False, use_cls=False, use_rec=True)
        if not result:
            return ""
        text, score = result[0][0], result[0][1]
        return str(text) if float(score) >= min_score else ""

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
