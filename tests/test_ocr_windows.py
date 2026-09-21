"""系统 OCR 的纯逻辑 + 引擎选择回退（Linux 上没有 WinRT，走假引擎）。"""
import numpy as np

from ivyea_translate import ocr_windows as ow
from ivyea_translate.ocr import OcrBlock, OcrEngine, OcrLine


def test_collapse_cjk_spaces_only_between_cjk():
    assert ow.collapse_cjk_spaces("微 信 桌 面 端") == "微信桌面端"
    assert ow.collapse_cjk_spaces("hello world") == "hello world"
    assert ow.collapse_cjk_spaces("本地 OCR 识别") == "本地 OCR 识别"     # 中英之间的空格保留
    assert ow.collapse_cjk_spaces("你好， 世界") == "你好，世界"


def test_line_box_from_words_is_union():
    assert ow.line_box_from_words([(10, 5, 20, 10), (40, 6, 15, 12)]) == (10, 5, 45, 13)


def test_available_is_false_off_windows():
    assert ow.available() is False


def test_engine_auto_falls_back_to_rapid_when_windows_unavailable(monkeypatch):
    eng = OcrEngine()
    eng.backend = "auto"
    called = {}

    def fake_rapid(arr, on_layout, on_block, near_gap=None, should_abort=None, trace=None):
        called["rapid"] = True
        on_layout([]); return 0

    monkeypatch.setattr(eng, "_recognize_rapid_streaming", fake_rapid)
    monkeypatch.setattr(ow, "available", lambda lang="": False)
    eng.recognize_streaming(np.zeros((10, 10, 3), np.uint8), lambda b: None, lambda i, b: None)
    assert called == {"rapid": True}


def test_engine_windows_path_groups_and_scales(monkeypatch):
    """系统 OCR 一步出行 → 同一套几何分段 → 坐标折回原图。"""
    eng = OcrEngine()
    eng.backend = "windows"
    monkeypatch.setattr(ow, "available", lambda lang="": True)
    # 输入小图会被放大 2 倍：假引擎在放大图坐标系里给两段
    def fake_lines(arr, lang=""):
        assert arr.shape[0] == 200 and arr.shape[1] == 600   # 100x300 放大 2 倍
        return [OcrLine("first line", 20, 20, 300, 30), OcrLine("second line", 20, 56, 300, 30),
                OcrLine("far para", 20, 160, 200, 30)]
    monkeypatch.setattr(ow, "recognize_lines", fake_lines)
    events = []
    n = eng.recognize_streaming(np.zeros((100, 300, 3), np.uint8),
                                lambda blocks: events.append(("layout", [(b.text, b.x, b.y) for b in blocks])),
                                lambda i, b: events.append(("block", i, b.text, b.x, b.y, b.w)))
    assert n == 2
    assert events[0] == ("layout", [("", 10, 10), ("", 10, 80)])
    assert events[1] == ("block", 0, "first line second line", 10, 10, 150)
    assert events[2] == ("block", 1, "far para", 10, 80, 100)


def test_engine_windows_failure_falls_back(monkeypatch):
    eng = OcrEngine()
    eng.backend = "auto"
    monkeypatch.setattr(ow, "available", lambda lang="": True)
    def boom(arr, lang=""):
        raise RuntimeError("winrt exploded")
    monkeypatch.setattr(ow, "recognize_lines", boom)
    called = {}
    monkeypatch.setattr(eng, "_recognize_rapid_streaming",
                        lambda *a, **k: called.setdefault("rapid", True) and 0)
    eng.recognize_streaming(np.zeros((10, 10, 3), np.uint8), lambda b: None, lambda i, b: None)
    assert called == {"rapid": True}


def test_engine_windows_only_mode_raises_when_unavailable(monkeypatch):
    import pytest
    eng = OcrEngine()
    eng.backend = "windows"
    monkeypatch.setattr(ow, "available", lambda lang="": False)
    with pytest.raises(RuntimeError):
        eng.recognize_streaming(np.zeros((10, 10, 3), np.uint8), lambda b: None, lambda i, b: None)
