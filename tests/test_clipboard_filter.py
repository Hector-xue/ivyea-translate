from ivyea_translate.clipboard_watch import is_double_copy


def test_double_copy_detected():
    assert is_double_copy("hello", "hello", 10.0, 9.6, 0.7)


def test_different_text_not_double():
    assert not is_double_copy("hello", "world", 10.0, 9.6, 0.7)


def test_timeout_not_double():
    assert not is_double_copy("hello", "hello", 10.0, 9.0, 0.7)


def test_empty_not_double():
    assert not is_double_copy("", "", 10.0, 9.9, 0.7)
    assert not is_double_copy(None, None, 10.0, 9.9, 0.7)
    assert not is_double_copy("   ", "   ", 10.0, 9.9, 0.7)


def test_same_event_not_double():
    # 同一次变更（时间差为 0）不算双击
    assert not is_double_copy("hello", "hello", 10.0, 10.0, 0.7)
