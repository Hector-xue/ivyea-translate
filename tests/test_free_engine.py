"""免费引擎的纯逻辑与引擎选择分发测试（不触网，CI 确定性）。

真实端点连通性由人工/发布前验证，不进单测：公开接口可能对 CI 出口 IP 风控。
"""
import pytest

from ivyea_translate.config import Config
from ivyea_translate.free_engine import (
    FreeEngine,
    free_engine,
    resolve_engine,
    split_for_translate,
)
from ivyea_translate.llm import LLMClient, LLMError


def test_split_short_no_change():
    assert split_for_translate("hello") == ["hello"]


def test_split_respects_max_and_preserves_content():
    text = "\n".join(["段落文字" * 60] * 5)  # 每段 240 字，5 段
    chunks = split_for_translate(text, 900)
    assert len(chunks) > 1
    assert all(len(c) <= 900 for c in chunks)
    # 去掉切块引入的换行后内容完整
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_split_hard_wraps_single_long_paragraph():
    text = "x" * 2500  # 单段无换行，必须硬切
    chunks = split_for_translate(text, 900)
    assert all(len(c) <= 900 for c in chunks)
    assert "".join(chunks) == text


def test_free_engine_has_is_free_flag():
    assert FreeEngine().is_free is True
    assert free_engine.is_free is True


def test_resolve_free_mode_returns_free(tmp_path):
    cfg = Config(tmp_path / "c.json")
    cfg.set("translate.engine", "free")
    cfg.set("provider.api_key", "sk-has-key")  # 即使配了 key，free 模式仍用免费
    assert resolve_engine(cfg) is free_engine


def test_resolve_auto_without_key_uses_free(tmp_path):
    cfg = Config(tmp_path / "c.json")  # 默认 auto，无 key
    assert resolve_engine(cfg) is free_engine


def test_resolve_auto_with_key_uses_llm(tmp_path):
    cfg = Config(tmp_path / "c.json")
    cfg.set("provider.api_key", "sk-abc")
    engine = resolve_engine(cfg)
    assert isinstance(engine, LLMClient)
    assert not getattr(engine, "is_free", False)


def test_resolve_llm_mode_without_key_raises(tmp_path):
    cfg = Config(tmp_path / "c.json")
    cfg.set("translate.engine", "llm")  # 强制大模型但没填 key
    with pytest.raises(LLMError):
        resolve_engine(cfg)


def test_engine_default_is_auto(tmp_path):
    cfg = Config(tmp_path / "c.json")
    assert cfg.get("translate.engine") == "auto"


def test_worker_uses_free_engine_nonstreaming(qapp):
    """免费引擎走非流式：chunk 一次给全量，finished_ok 给同一全量。"""
    import time

    from ivyea_translate.translator import TranslateWorker

    class FakeFree:
        is_free = True

        def translate(self, text, target_language):
            return f"[{target_language}]{text}"

    got = {}
    chunks = []
    worker = TranslateWorker(FakeFree(), "hello", "ja", "general")
    worker.chunk.connect(chunks.append)
    worker.finished_ok.connect(lambda full: got.__setitem__("full", full))
    worker.failed.connect(lambda m: got.__setitem__("err", m))
    worker.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and "full" not in got and "err" not in got:
        qapp.processEvents()
        time.sleep(0.01)
    worker.wait(2000)
    assert "err" not in got, got.get("err")
    assert got["full"] == "[ja]hello"
    assert chunks == ["[ja]hello"]
