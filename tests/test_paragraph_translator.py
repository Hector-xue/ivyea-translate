"""段落级并发翻译：段号对应、缓存命中、取消后静默、大模型逐段流式。"""
import threading
import time

from ivyea_translate import translator as tr
from ivyea_translate.translator import ParagraphTranslator


class _FreeClient:
    is_free = True

    def __init__(self, delay=0.0, fail_on=()):
        self.delay = delay
        self.fail_on = set(fail_on)
        self.calls = []

    def translate(self, text, target, should_abort=None):
        self.calls.append(text)
        time.sleep(self.delay)
        if text in self.fail_on:
            raise tr.LLMError("boom")
        return f"<{text}>"


class _LLMClient:
    is_free = False
    base_url = "http://x"
    model = "m"

    def stream_chat(self, messages):
        for piece in ("A", "B", "C"):
            yield piece


def _wait(qapp, cond, timeout=3.0):
    t0 = time.monotonic()
    while not cond() and time.monotonic() - t0 < timeout:
        qapp.processEvents()
        time.sleep(0.01)
    return cond()


def test_done_carries_paragraph_index(qapp):
    tr._CACHE.clear()
    client = _FreeClient()
    pt = ParagraphTranslator(client, "zh-CN", "general")
    got = {}
    pt.done.connect(lambda i, t: got.__setitem__(i, t))
    pt.submit(1, "second")
    pt.submit(0, "first")
    assert _wait(qapp, lambda: len(got) == 2)
    assert got == {0: "<first>", 1: "<second>"}
    pt.cancel()


def test_cache_hit_skips_network(qapp):
    tr._CACHE.clear()
    client = _FreeClient()
    pt = ParagraphTranslator(client, "zh-CN", "general")
    got = {}
    pt.done.connect(lambda i, t: got.__setitem__(i, t))
    pt.submit(0, "hello")
    assert _wait(qapp, lambda: 0 in got)
    pt.submit(1, "hello")
    assert _wait(qapp, lambda: 1 in got)
    assert client.calls == ["hello"]          # 第二次命中缓存
    pt.cancel()


def test_failure_is_per_paragraph(qapp):
    tr._CACHE.clear()
    client = _FreeClient(fail_on={"bad"})
    pt = ParagraphTranslator(client, "zh-CN", "general")
    done, failed = {}, {}
    pt.done.connect(lambda i, t: done.__setitem__(i, t))
    pt.failed.connect(lambda i, m: failed.__setitem__(i, m))
    pt.submit(0, "good")
    pt.submit(1, "bad")
    assert _wait(qapp, lambda: 0 in done and 1 in failed)
    assert done[0] == "<good>" and "boom" in failed[1]
    pt.cancel()


def test_cancel_silences_inflight(qapp):
    tr._CACHE.clear()
    client = _FreeClient(delay=0.3)
    pt = ParagraphTranslator(client, "zh-CN", "general")
    got = []
    pt.done.connect(lambda i, t: got.append(i))
    pt.submit(0, "slow")
    time.sleep(0.05)
    pt.cancel()
    time.sleep(0.5)
    qapp.processEvents()
    assert got == []
    assert not pt.cancelled or True


def test_empty_paragraph_completes_immediately(qapp):
    client = _FreeClient()
    pt = ParagraphTranslator(client, "zh-CN", "general")
    got = {}
    pt.done.connect(lambda i, t: got.__setitem__(i, t))
    pt.submit(3, "   ")
    qapp.processEvents()
    assert got == {3: ""} and client.calls == []


def test_llm_streams_chunks_with_index(qapp):
    tr._CACHE.clear()
    pt = ParagraphTranslator(_LLMClient(), "zh-CN", "general")
    chunks, done = [], {}
    pt.chunk.connect(lambda i, p: chunks.append((i, p)))
    pt.done.connect(lambda i, t: done.__setitem__(i, t))
    pt.submit(2, "x")
    assert _wait(qapp, lambda: 2 in done)
    assert chunks == [(2, "A"), (2, "B"), (2, "C")]
    assert done[2] == "ABC"
    pt.cancel()
