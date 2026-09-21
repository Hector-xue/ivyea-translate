"""本地模型：平台选包、断点续传+校验+换源、解压、安装判定、引擎解析、prompt 模板。"""
import hashlib
import io
import tarfile
import zipfile

import pytest

from ivyea_translate import local_model as lm
from ivyea_translate.llm import LLMError


# ---------- 纯函数 ----------

def test_platform_key_covers_supported_and_rejects_others():
    assert lm.platform_key("win32", "AMD64") == "win-x64"
    assert lm.platform_key("darwin", "arm64") == "mac-arm64"
    assert lm.platform_key("linux", "x86_64") == "linux-x64"
    assert lm.platform_key("darwin", "x86_64") is None
    assert lm.platform_key("win32", "ARM64") is None


def test_every_runtime_asset_has_pinned_sha256():
    for name, sha in lm.RUNTIME_ASSETS.values():
        assert lm.LLAMA_BUILD in name and len(sha) == 64
    for meta in lm.MODELS.values():
        assert len(meta["sha256"]) == 64 and meta["size"] > 0


def test_server_args_pin_parallel_slots_and_jinja(tmp_path):
    args = lm.server_args(tmp_path / "llama-server", tmp_path / "m.gguf", 12345, 4)
    assert args[:3] == [str(tmp_path / "llama-server"), "-m", str(tmp_path / "m.gguf")]
    assert "--port" in args and "12345" in args
    assert args[args.index("-np") + 1] == "3"        # 与段落并发数一致
    assert "--jinja" in args and "--no-webui" in args
    assert args[args.index("-t") + 1] == "4"


def test_summarize_backend_reads_gpu_or_cpu():
    gpu_log = (
        "ggml_vulkan: Found 1 Vulkan devices:\n"
        "ggml_vulkan: 0 = Intel(R) Iris(R) Xe Graphics (Intel open-source Mesa driver) | uma: 1 | fp16: 1\n"
        "load_tensors: offloaded 29/29 layers to GPU\n"
    )
    assert lm.summarize_backend(gpu_log) == "GPU · Intel(R) Iris(R) Xe Graphics (Intel open-source Mesa driver)"
    cpu_log = "load_tensors: offloaded 0/29 layers to GPU\nmodel loaded\n"
    assert lm.summarize_backend(cpu_log) == "CPU"
    assert lm.summarize_backend("") == ""


def test_urls_try_modelscope_then_hf_then_mirror():
    urls = lm.model_urls("hy-mt2-1.8b-q4")
    assert urls[0] == "https://modelscope.cn/models/Tencent-Hunyuan/Hy-MT2-1.8B-GGUF/resolve/master/Hy-MT2-1.8B-Q4_K_M.gguf"
    assert urls[1].startswith("https://huggingface.co/tencent/")
    assert urls[2].startswith("https://hf-mirror.com/tencent/")
    assert all(u.endswith("Hy-MT2-1.8B-Q4_K_M.gguf") for u in urls)


# ---------- 下载：续传 / 校验 / 换源 ----------

class _Resp:
    def __init__(self, status, body: bytes, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise IOError(f"http {self.status_code}")

    def iter_bytes(self, chunk_size=1024):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]


class _FakeHttp:
    """支持 Range 的假服务：每个 url 可配置 数据/失败/是否支持续传。"""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def stream(self, method, url, headers=None):
        self.calls.append((url, dict(headers or {})))
        spec = self.routes[url]
        if spec.get("fail"):
            raise IOError("connection reset")
        data = spec["data"]
        rng = (headers or {}).get("Range")
        if rng and spec.get("range", True):
            start = int(rng.split("=")[1].rstrip("-"))
            return _Resp(206, data[start:], {"Content-Length": str(len(data) - start)})
        return _Resp(200, data, {"Content-Length": str(len(data))})

    def close(self):
        pass


def test_download_resumes_from_part_and_verifies(tmp_path):
    data = bytes(range(256)) * 40
    sha = hashlib.sha256(data).hexdigest()
    dest = tmp_path / "m.gguf"
    part = tmp_path / "m.gguf.part"
    part.write_bytes(data[:3000])                      # 上次下到一半
    http = _FakeHttp({"https://a/x": {"data": data}})
    seen = []
    out = lm.download_file(["https://a/x"], dest, sha, progress=lambda d, t: seen.append((d, t)),
                           expected_size=len(data), client=http)
    assert out == dest and dest.read_bytes() == data and not part.exists()
    assert http.calls[0][1]["Range"] == "bytes=3000-"   # 续传
    assert seen[-1] == (len(data), len(data))


def test_download_falls_back_to_mirror_when_first_source_fails(tmp_path):
    data = b"hello" * 1000
    sha = hashlib.sha256(data).hexdigest()
    http = _FakeHttp({"https://a/x": {"fail": True}, "https://b/x": {"data": data}})
    lm.download_file(["https://a/x", "https://b/x"], tmp_path / "f", sha, client=http)
    assert [c[0] for c in http.calls] == ["https://a/x", "https://b/x"]
    assert (tmp_path / "f").read_bytes() == data


def test_download_restarts_when_server_ignores_range(tmp_path):
    data = b"abc" * 500
    sha = hashlib.sha256(data).hexdigest()
    part = tmp_path / "f.part"
    part.write_bytes(b"garbage")
    http = _FakeHttp({"https://a/x": {"data": data, "range": False}})
    lm.download_file(["https://a/x"], tmp_path / "f", sha, client=http)
    assert (tmp_path / "f").read_bytes() == data


def test_download_rejects_bad_hash_and_deletes_part(tmp_path):
    data = b"zzz" * 100
    http = _FakeHttp({"https://a/x": {"data": data}})
    with pytest.raises(LLMError, match="校验失败"):
        lm.download_file(["https://a/x"], tmp_path / "f", "0" * 64, client=http)
    assert not (tmp_path / "f").exists() and not (tmp_path / "f.part").exists()


def test_download_all_sources_failed_lists_them(tmp_path):
    http = _FakeHttp({"https://a/x": {"fail": True}, "https://b/x": {"fail": True}})
    with pytest.raises(LLMError, match="下载失败"):
        lm.download_file(["https://a/x", "https://b/x"], tmp_path / "f", "0" * 64, client=http, rounds=1)


def test_download_retries_rounds_with_resume(tmp_path, monkeypatch):
    """第一轮两个源都断，第二轮续上：不从零重下。"""
    monkeypatch.setattr(lm, "DOWNLOAD_RETRY_WAIT_S", 0.0)
    data = b"r" * 5000
    sha = hashlib.sha256(data).hexdigest()
    state = {"n": 0}

    class Flaky(_FakeHttp):
        def stream(self, method, url, headers=None):
            state["n"] += 1
            if state["n"] <= 2:
                raise IOError("reset")
            return super().stream(method, url, headers)

    http = Flaky({"https://a/x": {"data": data}, "https://b/x": {"data": data}})
    (tmp_path / "f.part").write_bytes(data[:1000])
    lm.download_file(["https://a/x", "https://b/x"], tmp_path / "f", sha, client=http, rounds=2)
    assert (tmp_path / "f").read_bytes() == data
    assert http.calls[-1][1]["Range"] == "bytes=1000-"      # 第二轮仍是续传


def test_has_local_files_sees_partial_download(local_dir):
    assert lm.has_local_files() is False
    part = lm.model_path("hy-mt2-1.8b-q4").with_suffix(".gguf.part")
    part.parent.mkdir(parents=True)
    part.write_bytes(b"x")
    assert lm.has_local_files() is True


def test_download_abort_keeps_part_for_resume(tmp_path):
    data = b"q" * 4096
    http = _FakeHttp({"https://a/x": {"data": data}})
    with pytest.raises(lm._Aborted):
        lm.download_file(["https://a/x"], tmp_path / "f", "0" * 64, client=http,
                         should_abort=lambda: True)
    assert (tmp_path / "f.part").exists()


# ---------- 解压 / 安装判定 ----------

def test_extract_zip_and_tar_and_find_binary(tmp_path):
    zpath = tmp_path / "rt.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("llama-server.exe" if lm._WINDOWS else "bin/llama-server", b"bin")
        z.writestr("ggml.dll", b"x")
    lm.extract_archive(zpath, tmp_path / "out1")
    assert lm.find_server_binary(tmp_path / "out1") is not None

    tpath = tmp_path / "rt.tar.gz"
    with tarfile.open(tpath, "w:gz") as t:
        info = tarfile.TarInfo("llama-b1/llama-server" if not lm._WINDOWS else "llama-b1/llama-server.exe")
        info.size = 3
        t.addfile(info, io.BytesIO(b"bin"))
    lm.extract_archive(tpath, tmp_path / "out2")
    binary = lm.find_server_binary(tmp_path / "out2")
    assert binary is not None
    if not lm._WINDOWS:
        assert binary.stat().st_mode & 0o111


@pytest.fixture()
def local_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(lm, "LOCAL_DIR", tmp_path / "local")
    monkeypatch.setattr(lm, "SERVER_LOG", tmp_path / "local" / "llama-server.log")
    return tmp_path / "local"


def _fake_install(local_dir, model_id="hy-mt2-1.8b-q4", size_ok=True):
    rt = local_dir / "runtime" / lm.LLAMA_BUILD
    rt.mkdir(parents=True)
    (rt / ("llama-server.exe" if lm._WINDOWS else "llama-server")).write_bytes(b"x")
    m = local_dir / "models" / lm.MODELS[model_id]["file"]
    m.parent.mkdir(parents=True, exist_ok=True)
    with open(m, "wb") as f:
        f.truncate(lm.MODELS[model_id]["size"] if size_ok else 10)


def test_installed_model_requires_runtime_and_full_size_model(local_dir):
    assert lm.installed_model() is None
    _fake_install(local_dir, size_ok=False)
    assert lm.installed_model() is None            # 半截文件不算
    with open(lm.model_path("hy-mt2-1.8b-q4"), "wb") as f:
        f.truncate(lm.MODELS["hy-mt2-1.8b-q4"]["size"])
    assert lm.installed_model() == "hy-mt2-1.8b-q4"
    assert "未安装" not in lm.installed_summary()


def test_installed_summary_reports_partial_download(local_dir):
    part = lm.model_path("hy-mt2-1.8b-q4").with_suffix(".gguf.part")
    part.parent.mkdir(parents=True)
    with open(part, "wb") as f:
        f.truncate(lm.MODELS["hy-mt2-1.8b-q4"]["size"] // 2)
    text = lm.installed_summary()
    assert text.startswith("未安装") and "50%" in text


# ---------- 引擎解析 ----------

def test_resolve_local_mode_without_install_raises(local_dir, tmp_path):
    from ivyea_translate.config import Config
    from ivyea_translate.free_engine import resolve_engine

    cfg = Config(tmp_path / "c.json")
    cfg.set("translate.engine", "local")
    with pytest.raises(LLMError, match="未下载"):
        resolve_engine(cfg)


def test_resolve_auto_uses_offline_fallback_only_when_installed(local_dir, tmp_path, monkeypatch):
    from ivyea_translate.config import Config
    from ivyea_translate import free_engine as fe

    cfg = Config(tmp_path / "c.json")
    assert fe.resolve_engine(cfg) is fe.free_engine
    _fake_install(local_dir)
    monkeypatch.setattr(lm.local_server, "start", lambda mid=None: None)   # 别真起子进程
    eng = fe.resolve_engine(cfg)
    assert isinstance(eng, fe.OfflineFallbackEngine) and eng.is_free
    cfg.set("translate.engine", "local")
    client = fe.resolve_engine(cfg)
    assert getattr(client, "is_local", False) and client.model == "hy-mt2-1.8b-q4"
    assert fe.uses_free_engine(cfg) is False


def test_offline_fallback_only_kicks_in_when_free_fails():
    from ivyea_translate.free_engine import OfflineFallbackEngine

    class Free:
        preferred = "google"
        def __init__(self, fail):
            self.fail = fail
            self.calls = 0
        def translate(self, text, target, should_abort=None):
            self.calls += 1
            if self.fail:
                raise LLMError("免费翻译暂不可用")
            return "在线译文"

    class Local:
        def __init__(self):
            self.msgs = None
        def chat(self, messages):
            self.msgs = messages
            return "本地译文"

    local = Local()
    ok = OfflineFallbackEngine(Free(fail=False), lambda: local)
    assert ok.translate("hi", "zh-CN") == "在线译文" and local.msgs is None
    down = OfflineFallbackEngine(Free(fail=True), lambda: local)
    assert down.translate("hi", "zh-CN") == "本地译文"
    assert local.msgs[0]["role"] == "user" and "Simplified Chinese" in local.msgs[0]["content"]
    # 取消不落到本地
    cancelled = OfflineFallbackEngine(type("F", (), {"preferred": None, "translate": lambda s, *a, **k: (_ for _ in ()).throw(LLMError("已取消"))})(), lambda: local)
    with pytest.raises(LLMError, match="已取消"):
        cancelled.translate("hi", "zh-CN")


# ---------- prompt / 客户端 ----------

def test_local_prompt_follows_official_template():
    from ivyea_translate.translator import build_local_messages, messages_for

    msgs = build_local_messages("今天天气真好。", "en", "general")
    assert len(msgs) == 1 and msgs[0]["role"] == "user"          # 官方：无 system prompt
    assert msgs[0]["content"].startswith("Translate the following text into English.")
    assert msgs[0]["content"].endswith("\n\n今天天气真好。")
    styled = build_local_messages("hi", "zh-CN", "formal")
    assert "translation style must strictly conform to [" in styled[0]["content"]
    # 美式仅英语目标生效
    assert "American" not in build_local_messages("hi", "zh-CN", "american")[0]["content"]

    class Local:
        is_local = True
    class Cloud:
        is_local = False
    assert len(messages_for(Local(), "x", "en", "general")) == 1
    assert messages_for(Cloud(), "x", "en", "general")[0]["role"] == "system"


def test_llm_client_sends_extra_body(monkeypatch):
    from ivyea_translate import llm

    captured = {}

    class FakeResp:
        status_code = 200
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def iter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":"ok"}}]}'
            yield "data: [DONE]"

    class FakeClient:
        def stream(self, method, url, headers=None, json=None, timeout=None):
            captured.update(json)
            return FakeResp()

    monkeypatch.setattr(llm, "_http_client", lambda base_url: FakeClient())
    c = llm.LLMClient("http://x/v1", "k", "m")
    c.extra_body = {"top_k": 20, "repeat_penalty": 1.05}
    assert "".join(c.stream_chat([{"role": "user", "content": "hi"}])) == "ok"
    assert captured["top_k"] == 20 and captured["repeat_penalty"] == 1.05 and captured["stream"] is True


# ---------- 设置卡 ----------

def test_settings_local_card_states(qapp, tmp_path, local_dir):
    from ivyea_translate.config import Config
    from ivyea_translate.ui.main_window import MainWindow

    win = MainWindow(Config(tmp_path / "c.json"))
    assert win.engine_combo.findData("local") >= 0
    assert win.local_install_btn.text() == "下载并启用"
    assert not win.local_remove_btn.isEnabled()
    # 下载失败留下半截文件：删除必须可点
    part = lm.model_path("hy-mt2-1.8b-q4").with_suffix(".gguf.part")
    part.parent.mkdir(parents=True)
    part.write_bytes(b"x")
    win._sync_local_buttons()
    assert win.local_install_btn.text() == "下载并启用" and win.local_remove_btn.isEnabled()
    assert win.local_progress_label.wordWrap()
    _fake_install(local_dir)
    win._sync_local_buttons()
    assert win.local_install_btn.text() == "重新下载"
    assert win.local_remove_btn.isEnabled()
    win.really_quit = True
    win.close()


def test_exit_reason_quotes_last_error_line(local_dir):
    local_dir.mkdir(parents=True, exist_ok=True)
    lm.SERVER_LOG.write_text(
        "0.00.089.086 E gguf_init_from_reader: tensor has offset 1, expected 2\n"
        "0.00.124.913 E srv  llama_server: exiting due to model loading error\n", encoding="utf-8")
    srv = lm.LocalServer()
    assert "exiting due to model loading error" in srv._exit_reason()
