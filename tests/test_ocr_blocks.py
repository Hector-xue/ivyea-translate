"""OCR 段落分块：文本、包围框、放大坐标归一化。"""
from ivyea_translate.ocr import (
    OcrLine,
    group_lines,
    merge_lines,
    recognize_blocks_from_result,
    scale_blocks,
)


def _lines():
    # 两段：第 1 段两行（行距小），空一大行后第 2 段一行
    return [
        OcrLine("Hello there", 10, 10, 100, 20),
        OcrLine("world again", 10, 32, 120, 20),
        OcrLine("Second para", 12, 120, 90, 18),
    ]


def test_group_lines_splits_paragraphs():
    blocks = group_lines(_lines())
    assert [b.text for b in blocks] == ["Hello there world again", "Second para"]


def test_group_lines_box_is_union_of_lines():
    first = group_lines(_lines())[0]
    assert (first.x, first.y) == (10, 10)
    assert first.x + first.w == 130   # max(110, 130)
    assert first.y + first.h == 52    # 32 + 20
    assert first.lines == 2
    assert first.line_h == 20


def test_merge_lines_still_matches_group_lines():
    """老链路（弹窗式）的文本必须和分块结果完全一致。"""
    assert merge_lines(_lines()) == "\n\n".join(b.text for b in group_lines(_lines()))


def test_group_lines_empty():
    assert group_lines([]) == []
    assert group_lines([OcrLine("  ", 0, 0, 1, 1)]) == []


def test_scale_blocks_folds_upscaled_coords_back():
    """OCR 为提高识别率把图放大了 2 倍，坐标必须折回原图尺度，
    否则原位翻译会把译文贴到两倍远的地方。"""
    blocks = group_lines([OcrLine("x", 100, 200, 60, 40)])
    back = scale_blocks(blocks, 2)
    assert (back[0].x, back[0].y, back[0].w, back[0].h) == (50, 100, 30, 20)
    assert back[0].line_h == 20


def test_scale_blocks_noop_when_not_upscaled():
    blocks = group_lines([OcrLine("x", 100, 200, 60, 40)])
    assert scale_blocks(blocks, 1)[0].x == 100


def test_recognize_blocks_normalizes_engine_output():
    """喂一份 RapidOCR 形状的假结果，验证解析 + 归一化整条链路。"""
    fake = [
        [[[20, 40], [220, 40], [220, 80], [20, 80]], "Hello", 0.99],
        [[[20, 200], [180, 200], [180, 236], [20, 236]], "World", 0.98],
    ]
    blocks = recognize_blocks_from_result(fake, scale=2)
    assert [b.text for b in blocks] == ["Hello", "World"]
    assert (blocks[0].x, blocks[0].y) == (10, 20)     # 除以放大倍数
    assert (blocks[0].w, blocks[0].h) == (100, 20)
