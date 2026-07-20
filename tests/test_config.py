import json

from ivyea_translate.config import DEFAULT_CONFIG, Config, _deep_merge


def test_defaults_when_no_file(tmp_path):
    cfg = Config(tmp_path / "config.json")
    assert cfg.get("provider.preset") == "deepseek"
    assert cfg.get("translate.target_language") == "auto"
    assert cfg.get("translate.primary_language") == "zh-CN"
    assert cfg.get("translate.secondary_language") == "en"
    assert cfg.get("hotkeys.screenshot_translate") == "<ctrl>+<alt>+s"
    assert cfg.get("hotkeys.select_translate") is None  # 划词热键已删除


def test_save_load_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    cfg = Config(path)
    cfg.set("provider.api_key", "sk-test-123")
    cfg.set("translate.style", "british")
    cfg.save()

    cfg2 = Config(path)
    assert cfg2.get("provider.api_key") == "sk-test-123"
    assert cfg2.get("translate.style") == "british"


def test_old_config_gains_new_default_keys(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"provider": {"api_key": "sk-old"}}), encoding="utf-8")
    cfg = Config(path)
    # 旧文件只有一个键，其余全部补默认
    assert cfg.get("provider.api_key") == "sk-old"
    assert cfg.get("provider.base_url") == DEFAULT_CONFIG["provider"]["base_url"]
    assert cfg.get("double_copy.max_chars") == 3000


def test_corrupt_config_falls_back_to_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    cfg = Config(path)
    assert cfg.get("provider.preset") == "deepseek"
    # 损坏文件不应被 load 覆盖
    assert path.read_text(encoding="utf-8") == "{not json"


def test_deep_merge_nested_override():
    merged = _deep_merge({"a": {"b": 1, "c": 2}}, {"a": {"b": 9}})
    assert merged == {"a": {"b": 9, "c": 2}}


def test_get_missing_returns_default(tmp_path):
    cfg = Config(tmp_path / "config.json")
    assert cfg.get("no.such.key", "fallback") == "fallback"
