"""OCR 流水线：识别前按几何分段、逐段回调、坐标折回原图。"""
import numpy as np

from ivyea_translate.ocr import (
    OcrBlock, OcrEngine, OcrLine, group_lines, lines_from_boxes, merge_near_blocks,
    plan_paragraphs,
)


def _line(y, h=20, x=10, w=200, text="t"):
    return OcrLine(text=text, x=x, y=y, w=w, h=h)


def test_plan_paragraphs_matches_legacy_grouping():
    """几何规划出的段落要和老链路 group_lines / merge_near_blocks 一致。"""
    lines = [_line(0), _line(24), _line(80), _line(104), _line(300)]
    plan = plan_paragraphs(lines)
    assert plan == [[[0, 1]], [[2, 3]], [[4]]]
    assert len(plan) == len(group_lines(lines))
    near = plan_paragraphs(lines, near_gap=1.8)
    # 行距 80-44=36 ≤ 1.8×20：前两段并成一张卡；第三段远，单独
    assert near == [[[0, 1], [2, 3]], [[4]]]
    assert len(near) == len(merge_near_blocks(group_lines(lines), 1.8))


def test_plan_paragraphs_sorts_by_reading_order():
    lines = [_line(300), _line(0), _line(24)]
    assert plan_paragraphs(lines) == [[[1, 2]], [[0]]]


def test_lines_from_boxes_uses_bounding_rect():
    ln = lines_from_boxes([[[20, 40], [220, 42], [220, 80], [20, 78]]])[0]
    assert (ln.x, ln.y, ln.w, ln.h) == (20, 40, 200, 40)
    assert ln.text == ""


class _FakeRapid:
    """模拟 RapidOCR 公开接口：整图 use_rec=False 给框；裁片 use_det=False 给文字。"""

    def __init__(self, boxes, texts):
        self.boxes = boxes
        self.texts = texts          # 按框顺序
        self.det_calls = 0
        self.rec_shapes = []

    def __call__(self, arr, use_det=None, use_cls=None, use_rec=None):
        if use_det:
            self.det_calls += 1
            return list(self.boxes), None
        self.rec_shapes.append(arr.shape)
        # 用裁片高度反查是哪一行（每行高度不同）
        h = arr.shape[0]
        for box, text in zip(self.boxes, self.texts):
            bh = box[2][1] - box[0][1]
            if abs(bh + 4 - h) <= 1:
                return [[text, 0.99]], None
        return None, None


def test_streaming_emits_layout_then_blocks_in_reading_order(monkeypatch):
    # 放大图（scale=2）坐标：两段，各一行，行高不同以便假引擎区分
    boxes = [
        [[20, 200], [400, 200], [400, 240], [20, 240]],   # 第二段（y 更大）
        [[20, 20], [400, 20], [400, 50], [20, 50]],       # 第一段
    ]
    fake = _FakeRapid(boxes, ["second", "first"])
    eng = OcrEngine()
    eng._engine = fake
    arr = np.full((150, 300, 3), 255, dtype=np.uint8)   # 小图 -> 放大 2 倍
    events = []
    n = eng.recognize_streaming(
        arr,
        on_layout=lambda blocks: events.append(("layout", [(b.text, b.x, b.y) for b in blocks])),
        on_block=lambda i, b: events.append(("block", i, b.text, b.x, b.y, b.w, b.h)),
    )
    assert n == 2 and fake.det_calls == 1
    assert events[0] == ("layout", [("", 10, 10), ("", 10, 100)])   # 折回原图坐标、无文字
    assert events[1] == ("block", 0, "first", 10, 10, 190, 15)
    assert events[2] == ("block", 1, "second", 10, 100, 190, 20)


def test_streaming_aborts_between_paragraphs():
    boxes = [[[0, 0], [100, 0], [100, 30], [0, 30]], [[0, 200], [100, 200], [100, 240], [0, 240]]]
    fake = _FakeRapid(boxes, ["a", "b"])
    eng = OcrEngine()
    eng._engine = fake
    arr = np.full((150, 300, 3), 255, dtype=np.uint8)
    got = []
    flips = iter([False, True])
    eng.recognize_streaming(arr, on_layout=lambda b: None,
                            on_block=lambda i, b: got.append(i),
                            should_abort=lambda: next(flips))
    assert got == [0]


def test_streaming_drops_low_confidence_line():
    class LowScore(_FakeRapid):
        def __call__(self, arr, use_det=None, use_cls=None, use_rec=None):
            if use_det:
                return list(self.boxes), None
            return [["garbage", 0.2]], None

    fake = LowScore([[[0, 0], [100, 0], [100, 30], [0, 30]]], ["x"])
    eng = OcrEngine()
    eng._engine = fake
    arr = np.full((150, 300, 3), 255, dtype=np.uint8)
    got = []
    eng.recognize_streaming(arr, on_layout=lambda b: None, on_block=lambda i, b: got.append(b.text))
    assert got == [""]
