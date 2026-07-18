from ivyea_translate.ocr import OcrLine, merge_lines


def line(text, x, y, w=200, h=20):
    return OcrLine(text=text, x=x, y=y, w=w, h=h)


def test_empty():
    assert merge_lines([]) == ""
    assert merge_lines([line("   ", 0, 0)]) == ""


def test_latin_lines_join_with_space():
    text = merge_lines([
        line("Hello world this is", 0, 0),
        line("a wrapped sentence.", 0, 24),
    ])
    assert text == "Hello world this is a wrapped sentence."


def test_cjk_lines_join_without_space():
    text = merge_lines([
        line("这是一段被换行", 0, 0),
        line("切开的中文文本。", 0, 24),
    ])
    assert text == "这是一段被换行切开的中文文本。"


def test_paragraph_split_on_large_gap():
    text = merge_lines([
        line("Paragraph one.", 0, 0),
        line("Paragraph two.", 0, 60),  # 间距 40 > 0.8*20
    ])
    assert text == "Paragraph one.\n\nParagraph two."


def test_hyphenated_wrap_joins_without_hyphen():
    text = merge_lines([
        line("interna-", 0, 0),
        line("tional", 0, 24),
    ])
    assert text == "international"


def test_out_of_order_input_sorted_by_y():
    text = merge_lines([
        line("second", 0, 24),
        line("first", 0, 0),
    ])
    assert text == "first second"
