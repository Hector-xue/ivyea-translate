from ivyea_translate.langdetect import choose_target, is_language


def test_is_language_chinese():
    assert is_language("人工智能正在改变世界", "zh-CN")
    assert not is_language("hello world", "zh-CN")
    assert not is_language("これはペンです", "zh-CN")   # 有假名 → 非中文


def test_is_language_english():
    assert is_language("hello world", "en")
    assert is_language("Café déjà vu résumé", "fr")     # 带重音的拉丁
    assert not is_language("人工智能", "en")


def test_is_language_others():
    assert is_language("こんにちは", "ja")
    assert is_language("안녕하세요", "ko")
    assert is_language("Привет мир", "ru")


def test_choose_target_zh_en_pair():
    # 中文 → 英文
    assert choose_target("人工智能正在改变世界", "zh-CN", "en") == "en"
    # 英文 → 中文
    assert choose_target("The quick brown fox", "zh-CN", "en") == "zh-CN"


def test_choose_target_third_language_goes_primary():
    # 第三种语言（法语）在 中↔英 对下 → 翻成主语言中文
    assert choose_target("Bonjour tout le monde", "zh-CN", "en") == "zh-CN"


def test_choose_target_japanese_not_mistaken_for_chinese():
    # 日语（含假名）不应被当成中文而误翻成英文
    assert choose_target("これはペンです", "zh-CN", "en") == "zh-CN"


def test_choose_target_numbers_only_falls_back_primary():
    assert choose_target("12345 !!!", "zh-CN", "en") == "zh-CN"


def test_choose_target_custom_pair():
    # 主日次英：日文→英，英文→日
    assert choose_target("こんにちは", "ja", "en") == "en"
    assert choose_target("hello", "ja", "en") == "ja"
