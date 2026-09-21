"""截图流水线会话：版面占位 → 逐段原文 → 逐段译文 → 落定；失败与取消路径。"""
import time

import numpy as np
import pytest

from ivyea_translate import screenshot_flow as sf
from ivyea_translate import translator as tr
from ivyea_translate.llm import LLMError
from ivyea_translate.ocr import OcrBlock


class _PopupView:
    def __init__(self):
        self.events = []

    def set_status(self, t): self.events.append(("status", t))
    def set_original(self, t): self.events.append(("original", t))
    def set_result_paragraphs(self, parts): self.events.append(("paras", list(parts)))
    def set_done(self, full): self.events.append(("done", full))
    def set_failed(self, msg): self.events.append(("failed", msg))


class _OverlayView(_PopupView):
    def prepare(self, blocks, recognized=True): self.events.append(("prepare", len(blocks), recognized))
    def set_block_source(self, i, b): self.events.append(("source", i, b.text))
    def set_block_text(self, i, t): self.events.append(("block", i, t))
    def finish(self): self.events.append(("finish",))
    def fail(self, msg, ms=0): self.events.append(("failed", msg))


class _FreeClient:
    is_free = True

    def __init__(self, fail_on=()):
        self.fail_on = set(fail_on)

    def translate(self, text, target, should_abort=None):
        if text in self.fail_on:
            raise LLMError("boom")
        return f"<{text}>"


def _fake_streaming(paras, layout_delay=0.0):
    """替换 ocr_engine.recognize_streaming：按给定段落文本模拟逐段交付。"""
    def run(arr, on_layout, on_block, near_gap=None, should_abort=None, trace=None):
        blocks = [OcrBlock(text="", x=0, y=i * 50, w=100, h=20) for i in range(len(paras))]
        on_layout(blocks)
        for i, text in enumerate(paras):
            if should_abort and should_abort():
                break
            on_block(i, OcrBlock(text=text, x=0, y=i * 50, w=100, h=20))
        return len(paras)
    return run


@pytest.fixture()
def fake_ocr(monkeypatch):
    def install(paras):
        monkeypatch.setattr(sf.ocr_engine, "recognize_streaming", _fake_streaming(paras))
        monkeypatch.setattr(sf, "qimage_to_rgb", lambda img: np.zeros((10, 10, 3), np.uint8))
    return install


def _run(qapp, session, view, until, timeout=3.0):
    session.start(object())
    t0 = time.monotonic()
    while not until(view) and time.monotonic() - t0 < timeout:
        qapp.processEvents()
        time.sleep(0.01)
    assert until(view), view.events


def _has(view, kind):
    return any(e[0] == kind for e in view.events)


def test_popup_flow_streams_original_and_paragraphs(qapp, fake_ocr):
    tr._CACHE.clear()
    fake_ocr(["first para", "second para"])
    view = _PopupView()
    finished = []
    s = sf.ScreenshotSession("popup", view, lambda: _FreeClient(),
                             target_for=lambda t: "zh-CN", style="general")
    s.finished.connect(lambda src, res, tgt: finished.append((src, res, tgt)))
    _run(qapp, s, view, lambda v: _has(v, "done"))
    kinds = [e[0] for e in view.events]
    assert kinds.index("original") < kinds.index("paras") < kinds.index("done")
    assert ("original", "first para\n\nsecond para") in view.events
    assert view.events[-1] == ("done", "<first para>\n\n<second para>")
    assert finished == [("first para\n\nsecond para", "<first para>\n\n<second para>", "zh-CN")]
    assert s.closed


def test_target_language_decided_by_first_paragraph(qapp, fake_ocr):
    tr._CACHE.clear()
    fake_ocr(["hello", "你好"])
    seen = []
    view = _PopupView()
    s = sf.ScreenshotSession("popup", view, lambda: _FreeClient(),
                             target_for=lambda t: seen.append(t) or "zh-CN", style="general")
    _run(qapp, s, view, lambda v: _has(v, "done"))
    assert seen == ["hello"]      # 只按首段判一次方向，全篇统一


def test_inplace_flow_prepares_layout_then_fills_blocks(qapp, fake_ocr):
    tr._CACHE.clear()
    fake_ocr(["a", "b"])
    view = _OverlayView()
    s = sf.ScreenshotSession("inplace", view, lambda: _FreeClient(),
                             target_for=lambda t: "en", style="general")
    _run(qapp, s, view, lambda v: _has(v, "finish"))
    assert view.events[1] == ("prepare", 2, False)     # 版面先到、还没有字
    assert ("source", 0, "a") in view.events and ("source", 1, "b") in view.events
    assert ("block", 0, "<a>") in view.events and ("block", 1, "<b>") in view.events
    assert view.events[-1] == ("finish",)


def test_empty_paragraphs_do_not_block_completion(qapp, fake_ocr):
    tr._CACHE.clear()
    fake_ocr(["", "only", ""])
    view = _PopupView()
    s = sf.ScreenshotSession("popup", view, lambda: _FreeClient(),
                             target_for=lambda t: "zh-CN", style="general")
    _run(qapp, s, view, lambda v: _has(v, "done"))
    assert view.events[-1] == ("done", "<only>")


def test_no_text_at_all_fails_cleanly(qapp, fake_ocr):
    fake_ocr(["", ""])
    view = _PopupView()
    s = sf.ScreenshotSession("popup", view, lambda: _FreeClient(),
                             target_for=lambda t: "zh-CN", style="general")
    _run(qapp, s, view, lambda v: _has(v, "failed"))
    assert ("failed", "没有识别到文字") in view.events


def test_engine_unavailable_fails_once(qapp, fake_ocr):
    def factory():
        raise LLMError("请先在设置里填写 API Key")

    fake_ocr(["x", "y"])
    view = _PopupView()
    s = sf.ScreenshotSession("popup", view, factory, target_for=lambda t: "zh-CN", style="general")
    _run(qapp, s, view, lambda v: _has(v, "failed"))
    assert [e for e in view.events if e[0] == "failed"] == [("failed", "请先在设置里填写 API Key")]


def test_partial_failure_is_inline_not_fatal(qapp, fake_ocr):
    tr._CACHE.clear()
    fake_ocr(["good", "bad"])
    view = _PopupView()
    s = sf.ScreenshotSession("popup", view, lambda: _FreeClient(fail_on={"bad"}),
                             target_for=lambda t: "zh-CN", style="general")
    _run(qapp, s, view, lambda v: _has(v, "done"))
    assert view.events[-1][1] == "<good>\n\n[翻译失败：boom]"


def test_all_failed_reports_failure(qapp, fake_ocr):
    tr._CACHE.clear()
    fake_ocr(["bad"])
    view = _PopupView()
    s = sf.ScreenshotSession("popup", view, lambda: _FreeClient(fail_on={"bad"}),
                             target_for=lambda t: "zh-CN", style="general")
    _run(qapp, s, view, lambda v: _has(v, "failed"))
    assert ("failed", "翻译失败：boom") in view.events


def test_close_stops_further_updates(qapp, fake_ocr):
    tr._CACHE.clear()
    fake_ocr(["a", "b"])
    view = _PopupView()
    s = sf.ScreenshotSession("popup", view, lambda: _FreeClient(),
                             target_for=lambda t: "zh-CN", style="general")
    s.close()
    s.start(object())
    time.sleep(0.2)
    qapp.processEvents()
    assert not _has(view, "done") and not _has(view, "paras")


def test_join_paragraphs_skips_pending_and_empty():
    assert sf.join_paragraphs(["a", None, "", "b"]) == "a\n\nb"
