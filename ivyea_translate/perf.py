"""截图翻译全链路计时：一条日志看清时间花在哪一段。

没有数字就没法说"快了"。Trace 记录热键按下→框选完成→版面检测→首段识别→
首段译文→全部完成 的时间点，结束时打成一行：

    截图翻译耗时 1.21s：框选 0.00 → 检测 +0.31 → 首段识别 +0.18 → 首段译文 +0.22 → 完成 +0.50

线程安全（OCR 线程与翻译线程都会打点）；同名打点只记第一次（"首段"语义）。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)


class Trace:
    def __init__(self, name: str):
        self.name = name
        self._t0 = time.monotonic()
        self._marks: List[Tuple[str, float]] = []
        self._lock = threading.Lock()
        self._closed = False

    def mark(self, label: str) -> None:
        """记录一个阶段完成时刻；同名只记第一次。"""
        with self._lock:
            if self._closed or any(l == label for l, _ in self._marks):
                return
            self._marks.append((label, time.monotonic() - self._t0))

    def elapsed(self, label: Optional[str] = None) -> Optional[float]:
        with self._lock:
            if label is None:
                return time.monotonic() - self._t0
            for l, t in self._marks:
                if l == label:
                    return t
        return None

    def summary(self) -> str:
        with self._lock:
            marks = list(self._marks)
        total = marks[-1][1] if marks else time.monotonic() - self._t0
        parts = []
        prev = 0.0
        for i, (label, t) in enumerate(marks):
            parts.append(f"{label} {t:.2f}" if i == 0 else f"{label} +{t - prev:.2f}")
            prev = t
        return f"{self.name}耗时 {total:.2f}s：" + " → ".join(parts)

    def finish(self, label: str = "完成") -> str:
        """收尾：打最后一个点并输出日志（幂等，只输出一次）。"""
        self.mark(label)
        with self._lock:
            if self._closed:
                return ""
            self._closed = True
        text = self.summary()
        log.info(text)
        return text
