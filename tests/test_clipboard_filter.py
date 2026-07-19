from ivyea_translate.clipboard_watch import should_translate


def test_normal_text_passes():
    assert should_translate("hello world", None, None, 3000)


def test_empty_and_whitespace_rejected():
    assert not should_translate(None, None, None, 3000)
    assert not should_translate("", None, None, 3000)
    assert not should_translate("   \n ", None, None, 3000)


def test_duplicate_of_last_rejected():
    assert not should_translate("same", "same", None, 3000)


def test_own_copy_rejected():
    assert not should_translate("译文内容", None, "译文内容", 3000)


def test_over_length_rejected():
    assert not should_translate("x" * 3001, None, None, 3000)
    assert should_translate("x" * 3000, None, None, 3000)


def test_zero_max_chars_means_unlimited():
    assert should_translate("x" * 99999, None, None, 0)


def test_double_copy_detection():
    from ivyea_translate.clipboard_watch import is_double_copy
    assert is_double_copy("hello", "hello", 10.0, 9.6, 0.7)
    assert not is_double_copy("hello", "world", 10.0, 9.6, 0.7)   # 文本不同
    assert not is_double_copy("hello", "hello", 10.0, 9.0, 0.7)   # 超时
    assert not is_double_copy("", "", 10.0, 9.9, 0.7)             # 空文本
    assert not is_double_copy("hello", "hello", 10.0, 10.0, 0.7)  # 同一次变更
