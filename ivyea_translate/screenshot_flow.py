"""截图翻译流水线：框选 → 版面检测 → 逐段识别 → 逐段翻译 → 逐段上屏。

以前是三段串行等待：整图识完 → 整段一次请求 → 回来才显示。现在一次截图是一个
ScreenshotSession：OCR 线程检测完版面就交给界面占位，然后逐段识别、每段识完立刻
送翻译（并发 3），译文到一段贴一段。首段译文出现的时间 ≈ 检测 + 首段识别 + 一个
往返，其余段落的识别与翻译全部重叠在后面。

弹窗模式与原位模式共用同一条流水线，只是"视图"不同（鸭子类型）：
  弹窗：set_status / set_original / set_result_paragraphs / set_done / set_failed
  原位：prepare(blocks, recognized=False) / set_block_source / set_block_text / finish / fail

目标语言由第一段识别出的文字决定（自动方向），全篇统一——同一张截图不该一段译成
中文一段译成英文。
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, List, Optional

from PySide6.QtCore import QObject, Signal

from .llm import LLMError
from .ocr import OcrBlock, ocr_engine, qimage_to_rgb
from .perf import Trace
from .translator import ParagraphTranslator

log = logging.getLogger(__name__)

INPLACE_NEAR_GAP = 1.8   # 原位模式：挨得近的段并成一张卡（见 ocr.merge_near_blocks）


def join_paragraphs(parts: List[Optional[str]]) -> str:
    """已到的段落按序拼成整篇（纯函数）：未到/空段跳过。"""
    return "\n\n".join(p for p in parts if p)


class ScreenshotSession(QObject):
    """一次截图翻译的完整生命周期。view 关闭即取消一切在飞工作。"""

    # OCR 线程 -> 主线程
    _layout_ready = Signal(list)
    _block_ready = Signal(int, object)
    _ocr_finished = Signal(int)
    _ocr_failed = Signal(str)
    # 对外：整篇完成（原文, 译文, 目标语言），供写历史
    finished = Signal(str, str, str)

    def __init__(self, mode: str, view, client_factory: Callable[[], object],
                 target_for: Callable[[str], str], style: str, parent=None):
        super().__init__(parent)
        assert mode in ("popup", "inplace")
        self.mode = mode
        self.view = view
        self._client_factory = client_factory
        self._target_for = target_for
        self._style = style
        self.trace = Trace("截图翻译" if mode == "popup" else "原位翻译")
        self.thread: Optional[threading.Thread] = None
        self._closed = False
        self._translator: Optional[ParagraphTranslator] = None
        self._target = ""
        self._sources: List[Optional[str]] = []
        self._results: List[Optional[str]] = []
        self._partials: dict = {}          # 段号 -> 流式累计（仅大模型）
        self._ocr_done = False
        self._failures = 0
        self._layout_ready.connect(self._on_layout)
        self._block_ready.connect(self._on_block)
        self._ocr_finished.connect(self._on_ocr_finished)
        self._ocr_failed.connect(self._on_ocr_failed)

    # ---- 生命周期 ----

    @property
    def inplace(self) -> bool:
        return self.mode == "inplace"

    @property
    def closed(self) -> bool:
        return self._closed

    def start(self, image) -> None:
        """image：QImage（可安全跨线程；QPixmap 不行，转换在主线程做完再进来）。"""
        self.trace.mark("框选")
        if self.inplace:
            self.view.set_status("识别中…")
        else:
            self.view.set_status("正在识别文字…")
        self.thread = threading.Thread(target=self._ocr_run, args=(image,), daemon=True)
        self.thread.start()

    def close(self) -> None:
        """视图没了 / 用户取消：停止发信号，取消在飞翻译，OCR 线程在段落边界退出。"""
        if self._closed:
            return
        self._closed = True
        if self._translator is not None:
            self._translator.cancel()

    # ---- OCR 线程 ----

    def _ocr_run(self, image) -> None:
        try:
            n = ocr_engine.recognize_streaming(
                qimage_to_rgb(image),
                on_layout=lambda blocks: self._layout_ready.emit(list(blocks)),
                on_block=lambda idx, block: self._block_ready.emit(idx, block),
                near_gap=INPLACE_NEAR_GAP if self.inplace else None,
                should_abort=lambda: self._closed,
                trace=self.trace,
            )
            self._ocr_finished.emit(n)
        except Exception as e:  # 引擎加载失败等：整次失败
            self._ocr_failed.emit(str(e))

    # ---- 主线程槽 ----

    def _on_layout(self, blocks: List[OcrBlock]) -> None:
        if self._closed:
            return
        n = len(blocks)
        self._sources = [None] * n
        self._results = [None] * n
        if n == 0:
            self._fail("没有识别到文字")
            return
        if self.inplace:
            self.view.prepare(blocks, recognized=False)

    def _on_block(self, idx: int, block: OcrBlock) -> None:
        if self._closed or not (0 <= idx < len(self._sources)):
            return
        text = (block.text or "").strip()
        self._sources[idx] = text
        if self.inplace:
            self.view.set_block_source(idx, block)
        else:
            self.view.set_original(join_paragraphs(self._sources))
            if text:
                self.view.set_status("翻译中…")
        if not text:
            self._results[idx] = ""
            self._check_complete()
            return
        if self._translator is None and not self._ensure_translator(text):
            return
        self._translator.submit(idx, text)

    def _ensure_translator(self, first_text: str) -> bool:
        try:
            client = self._client_factory()
        except LLMError as e:
            self._fail(str(e))
            return False
        self._target = self._target_for(first_text)
        self._translator = ParagraphTranslator(client, self._target, self._style, parent=self)
        self._translator.chunk.connect(self._on_chunk)
        self._translator.done.connect(self._on_done)
        self._translator.failed.connect(self._on_failed)
        return True

    def _on_chunk(self, idx: int, piece: str) -> None:
        if self._closed or self.inplace:
            return  # 原位卡片按最终文本排版，不逐字重排
        self._partials[idx] = self._partials.get(idx, "") + piece
        self._render_popup()

    def _on_done(self, idx: int, text: str) -> None:
        if self._closed or not (0 <= idx < len(self._results)):
            return
        self.trace.mark("首段译文")
        self._results[idx] = text
        self._partials.pop(idx, None)
        if self.inplace:
            self.view.set_block_text(idx, text)
        else:
            self._render_popup()
        self._check_complete()

    def _on_failed(self, idx: int, message: str) -> None:
        if self._closed or not (0 <= idx < len(self._results)):
            return
        log.info("第 %d 段翻译失败：%s", idx, message)
        self._failures += 1
        self._results[idx] = f"[翻译失败：{message}]"
        self._partials.pop(idx, None)
        if self.inplace:
            self.view.set_status("部分段落翻译失败")
        else:
            self._render_popup()
        self._check_complete()

    def _on_ocr_finished(self, n: int) -> None:
        if self._closed:
            return
        self._ocr_done = True
        if self._sources and all(not s for s in self._sources):
            self._fail("没有识别到文字")
            return
        self._check_complete()

    def _on_ocr_failed(self, message: str) -> None:
        if not self._closed:
            self._fail(f"识别失败：{message}")

    # ---- 汇总 ----

    def _render_popup(self) -> None:
        parts: List[Optional[str]] = []
        for i, r in enumerate(self._results):
            if r is None and i in self._partials:
                parts.append(self._partials[i])
            elif r == "":
                continue  # 空段不占位
            else:
                parts.append(r)
        self.view.set_result_paragraphs(parts)

    def _check_complete(self) -> None:
        if not self._ocr_done or any(r is None for r in self._results):
            return
        translated = [r for r in self._results if r]
        if translated and self._failures == len(translated):
            self._fail("翻译失败：" + translated[0].strip("[]").replace("翻译失败：", ""))
            return
        full = join_paragraphs(self._results)
        source = join_paragraphs(self._sources)
        if self.inplace:
            self.view.finish()
        else:
            self.view.set_done(full)
        self.trace.finish()
        self._closed = True
        self.finished.emit(source, full, self._target)

    def _fail(self, message: str) -> None:
        if self.inplace:
            self.view.fail(message, 3000)
        else:
            self.view.set_failed(message)
        self.trace.finish("失败")
        self.close()
