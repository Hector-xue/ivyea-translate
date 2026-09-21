"""本地离线翻译模型：腾讯混元 Hy-MT2-1.8B（GGUF）+ llama.cpp 的 llama-server 子进程。

为什么这样接而不是引 llama-cpp-python：
- llama-server 对外就是 OpenAI 兼容接口，现有 LLMClient（流式/缓存/段落并发）零改动直接用；
- 运行时和模型都按需下载到用户目录，主安装包一字节不涨，也不用碰 PyInstaller 打包
  原生扩展（CPU/GPU 变体各一份轮子，是打包地狱）；
- Windows 用 Vulkan 版：Intel/AMD/NVIDIA 核显独显通吃，没有 Vulkan 设备时 ggml 自动
  退到 CPU 后端（后端是动态加载的），不需要用户选。

诚实的速度：1.8B Q4 在近两年笔记本 CPU 上约 15-30 tok/s，一段截图（约 80 个中文
token）1.5-4s，但流式首字 0.2-0.5s 就出；核显翻倍；短句接近秒出。定位是**离线 +
隐私 + 不限流**，长段落不会比免费引擎的热连接更快，界面文案不许把它说成"更快"。

目录：~/.ivyea-translate/local/
    runtime/<build>/…      llama-server 及其动态库（按平台解压的官方 release 包）
    models/<file>.gguf     模型
    llama-server.log       子进程日志（含设备/后端信息，状态栏据此显示 GPU/CPU）

完整性：运行时与模型的 sha256 都钉在代码里，下载源可以走镜像（国内直连
huggingface/github 常不通），校验不过一律删掉重来，镜像不可信也没关系。
"""
from __future__ import annotations

import hashlib
import logging
import os
import platform
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import httpx

from .config import CONFIG_DIR
from .llm import LLMClient, LLMError

log = logging.getLogger(__name__)

_WINDOWS = sys.platform == "win32"

LLAMA_BUILD = "b11065"   # 钉死构建号：资产名与 sha256 一一对应，升级时一起改

# 平台 -> (资产文件名, sha256)。Windows 只发 Vulkan 版（无 Vulkan 设备自动退 CPU）
RUNTIME_ASSETS: Dict[str, Tuple[str, str]] = {
    "win-x64": (f"llama-{LLAMA_BUILD}-bin-win-vulkan-x64.zip",
                "12733edc26574d117cb189fb5c95323d37a94d1d27123f540f788b328cf9d68b"),
    "mac-arm64": (f"llama-{LLAMA_BUILD}-bin-macos-arm64.tar.gz",
                  "373ec166e1f40a4b3be1da6c9a2765a01195816fabb869851129ba9c02393d4a"),
    "linux-x64": (f"llama-{LLAMA_BUILD}-bin-ubuntu-x64.tar.gz",
                  "f00971c1b044fae179230bfc6f8d9f8461b778fef9ffac2b450088081a8ecd43"),
}

# 模型目录：id -> 元数据。size/sha256 来自 HuggingFace LFS 指针（2026-09-21 核对）
MODELS: Dict[str, Dict] = {
    "hy-mt2-1.8b-q4": {
        "label": "Hy-MT2 1.8B · Q4（约 1.1GB，推荐）",
        "repo": "tencent/Hy-MT2-1.8B-GGUF",
        "file": "Hy-MT2-1.8B-Q4_K_M.gguf",
        "size": 1133080448,
        "sha256": "dc5f44fcf1fa496ee7ad725982c0c8c553a4de00259b53af84c4b89fb0c06699",
    },
    # 不收 2-bit / 1.25-bit 版：它们依赖腾讯的 STQ 量化内核（llama.cpp PR #19357），
    # 官方 release 二进制装不上——本机实测 b11065 报 "tensor offset mismatch"。等合入再加。
}
DEFAULT_MODEL = "hy-mt2-1.8b-q4"

# 下载源按顺序试（连不上/中断就换下一个续传）。sha256 钉死，镜像不可信也无妨
HF_HOSTS = ["https://huggingface.co", "https://hf-mirror.com"]
GITHUB_HOSTS = ["https://github.com", "https://ghfast.top/https://github.com"]

LOCAL_DIR = CONFIG_DIR / "local"
SERVER_LOG = LOCAL_DIR / "llama-server.log"

# 官方推荐的 1.8B/7B 推理参数（README：模型没有默认 system prompt）
GEN_PARAMS = {"temperature": 0.7, "top_p": 0.6, "top_k": 20, "repeat_penalty": 1.05}
READY_TIMEOUT_S = 120.0   # 首次加载模型（1GB 从磁盘读进内存）可能要十几秒；机械盘更久


# ---------- 平台 / 路径 ----------

def platform_key(system: str = sys.platform, machine: str = platform.machine()) -> Optional[str]:
    """纯函数：(sys.platform, machine) -> RUNTIME_ASSETS 的键；不支持返回 None。"""
    m = machine.lower()
    if system == "win32":
        return "win-x64" if m in ("amd64", "x86_64") else None
    if system == "darwin":
        return "mac-arm64" if m in ("arm64", "aarch64") else None
    if system.startswith("linux"):
        return "linux-x64" if m in ("x86_64", "amd64") else None
    return None


def runtime_asset() -> Tuple[str, str]:
    key = platform_key()
    if key is None:
        raise LLMError(f"本地模型暂不支持此平台（{sys.platform}/{platform.machine()}）")
    return RUNTIME_ASSETS[key]


def runtime_dir() -> Path:
    return LOCAL_DIR / "runtime" / LLAMA_BUILD


def models_dir() -> Path:
    return LOCAL_DIR / "models"


def model_path(model_id: str) -> Path:
    return models_dir() / MODELS[model_id]["file"]


def find_server_binary(root: Path) -> Optional[Path]:
    """在解压目录里找 llama-server（release 包的目录层级各平台不同）。"""
    name = "llama-server.exe" if _WINDOWS else "llama-server"
    if not root.exists():
        return None
    for p in root.rglob(name):
        if p.is_file():
            return p
    return None


def runtime_ready() -> bool:
    return find_server_binary(runtime_dir()) is not None


def model_ready(model_id: str) -> bool:
    """模型文件在且大小对（下载中断的 .part 不算）。"""
    p = model_path(model_id)
    try:
        return p.is_file() and p.stat().st_size == MODELS[model_id]["size"]
    except (KeyError, OSError):
        return False


def installed_model() -> Optional[str]:
    """已就绪的模型 id（有多个时取推荐款）；运行时或模型缺一不可。"""
    if not runtime_ready():
        return None
    for mid in [DEFAULT_MODEL] + [m for m in MODELS if m != DEFAULT_MODEL]:
        if model_ready(mid):
            return mid
    return None


def runtime_urls() -> List[str]:
    name, _ = runtime_asset()
    return [f"{host}/ggml-org/llama.cpp/releases/download/{LLAMA_BUILD}/{name}" for host in GITHUB_HOSTS]


def model_urls(model_id: str) -> List[str]:
    meta = MODELS[model_id]
    return [f"{host}/{meta['repo']}/resolve/main/{meta['file']}" for host in HF_HOSTS]


# ---------- 下载 / 校验 / 解压 ----------

def sha256_of(path: Path, chunk: int = 4 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def download_file(urls: List[str], dest: Path, sha256: str,
                  progress: Optional[Callable[[int, int], None]] = None,
                  should_abort: Optional[Callable[[], bool]] = None,
                  expected_size: int = 0,
                  client: Optional[httpx.Client] = None) -> Path:
    """多源断点续传下载到 dest，最后校验 sha256（不过就删）。

    .part 文件保留进度：网断了、用户关了、换镜像了都从已下载的字节续；
    某个源不支持 Range（返回 200 而非 206）就从头下。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=True,
                              headers={"User-Agent": "IvyeaTranslate"})
    errors: List[str] = []
    try:
        for url in urls:
            try:
                _download_one(client, url, part, progress, should_abort, expected_size)
                break
            except _Aborted:
                raise
            except Exception as e:
                errors.append(f"{url.split('/')[2]}: {e.__class__.__name__}: {str(e)[:80]}")
                log.info("下载源失败，换下一个：%s", errors[-1])
        else:
            raise LLMError("下载失败（" + "；".join(errors) + "）")
    finally:
        if own_client:
            client.close()
    got = sha256_of(part)
    if got.lower() != sha256.lower():
        part.unlink(missing_ok=True)
        raise LLMError("下载文件校验失败（sha256 不符），已删除，请重试")
    os.replace(part, dest)
    return dest


class _Aborted(Exception):
    pass


def _download_one(client: httpx.Client, url: str, part: Path,
                  progress, should_abort, expected_size: int) -> None:
    have = part.stat().st_size if part.exists() else 0
    if expected_size and have >= expected_size:
        return  # 上次已经下完只差校验
    headers = {"Range": f"bytes={have}-"} if have else {}
    with client.stream("GET", url, headers=headers) as resp:
        if resp.status_code == 416:  # 服务端认为已完整
            return
        resp.raise_for_status()
        mode = "ab"
        if have and resp.status_code != 206:
            have, mode = 0, "wb"  # 不支持续传：从头来
        total = have + int(resp.headers.get("Content-Length", 0) or 0)
        if not total and expected_size:
            total = expected_size
        done = have
        last_emit = 0.0
        with open(part, mode) as f:
            for chunk in resp.iter_bytes(chunk_size=512 * 1024):
                if should_abort is not None and should_abort():
                    raise _Aborted()
                f.write(chunk)
                done += len(chunk)
                now = time.monotonic()
                if progress is not None and (now - last_emit > 0.1 or done == total):
                    last_emit = now
                    progress(done, total)
        if total and done < total:
            raise IOError(f"下载不完整 {done}/{total}")


def extract_archive(archive: Path, dest: Path) -> None:
    """解压 zip / tar.gz 到 dest（先清空），非 Windows 给 llama-server 加执行位。"""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
    else:
        with tarfile.open(archive, "r:gz") as t:
            try:
                t.extractall(dest, filter="data")   # 3.12+：拒绝越界路径
            except TypeError:
                t.extractall(dest)
    if not _WINDOWS:
        for p in dest.rglob("*"):
            if p.is_file() and (p.name.startswith("llama-") or p.suffix in ("", ".so", ".dylib")):
                try:
                    p.chmod(p.stat().st_mode | 0o111)
                except OSError:
                    pass


# ---------- 子进程 ----------

def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def server_args(binary: Path, model: Path, port: int, threads: int) -> List[str]:
    """纯函数：llama-server 启动参数。

    -ngl 99 全部层上 GPU（CPU 版/无设备时忽略）；-np 3 与段落并发数一致，否则
    三段并发在服务端排队；-c 8192 给三槽各 2.7k 上下文，截图长段够用；
    --jinja 用 GGUF 内嵌的聊天模板（Hunyuan 模板需要 jinja 渲染）。
    """
    return [
        str(binary), "-m", str(model), "--host", "127.0.0.1", "--port", str(port),
        "-c", "8192", "-np", "3", "-ngl", "99", "-t", str(threads),
        "--jinja", "--no-webui", "--log-timestamps",
    ]


def summarize_backend(log_text: str) -> str:
    """纯函数：从 llama-server 日志里读出跑在什么设备上，给状态栏显示。"""
    gpu = ""
    for line in log_text.splitlines():
        low = line.lower()
        if "ggml_vulkan" in low and "|" in line and "device" not in low.split("|")[0]:
            # 形如 "ggml_vulkan: 0 = Intel(R) Iris(R) Xe Graphics (Intel open-source ...) | uma: 1 | ..."
            try:
                gpu = line.split("=", 1)[1].split("|", 1)[0].strip()
            except IndexError:
                pass
        elif "ggml_metal" in low and "gpu name" in low:
            gpu = line.split(":", 1)[1].strip()
    for line in log_text.splitlines():
        if "offloaded" in line and "layers to gpu" in line.lower():
            try:
                frac = line.split("offloaded", 1)[1].split("layers", 1)[0].strip()
                n = int(frac.split("/")[0])
            except (ValueError, IndexError):
                n = 0
            if n > 0:
                return f"GPU · {gpu}" if gpu else "GPU"
            return "CPU"
    return "CPU" if log_text else ""


class LocalServer:
    """llama-server 子进程的生命周期：起、等就绪、给客户端、停。线程安全。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._port = 0
        self._model_id = ""
        self._ready = threading.Event()
        self._fail_reason = ""

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def ready(self) -> bool:
        return self.running and self._ready.is_set()

    @property
    def model_id(self) -> str:
        return self._model_id

    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}/v1"

    def start(self, model_id: Optional[str] = None) -> None:
        """异步启动（已在跑且是同一模型则不动）。就绪与否看 wait_ready。"""
        with self._lock:
            model_id = model_id or installed_model()
            if model_id is None:
                raise LLMError("本地模型未下载：设置 → 翻译模型 → 本地模型 → 下载并启用")
            if self.running and self._model_id == model_id:
                return
            self._stop_locked()
            binary = find_server_binary(runtime_dir())
            if binary is None or not model_ready(model_id):
                raise LLMError("本地模型文件不完整，请到设置里重新下载")
            self._port = free_port()
            self._model_id = model_id
            self._ready.clear()
            self._fail_reason = ""
            threads = max(1, min(8, (os.cpu_count() or 4) - 1))
            args = server_args(binary, model_path(model_id), self._port, threads)
            LOCAL_DIR.mkdir(parents=True, exist_ok=True)
            logf = open(SERVER_LOG, "w", encoding="utf-8", errors="replace")
            kwargs = {}
            if _WINDOWS:
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            log.info("启动本地模型服务：%s", " ".join(args))
            self._proc = subprocess.Popen(
                args, cwd=str(binary.parent), stdout=logf, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, **kwargs)
            threading.Thread(target=self._watch_ready, args=(self._proc,), daemon=True).start()

    def _watch_ready(self, proc: subprocess.Popen) -> None:
        deadline = time.monotonic() + READY_TIMEOUT_S
        url = f"http://127.0.0.1:{self._port}/health"
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                self._fail_reason = f"本地模型服务退出（code {proc.returncode}），详见 {SERVER_LOG}"
                log.warning(self._fail_reason)
                return
            try:
                if httpx.get(url, timeout=2.0).status_code == 200:
                    self._ready.set()
                    log.info("本地模型就绪：%s（%s）", self._model_id, self.backend_summary())
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.3)
        self._fail_reason = "本地模型加载超时"
        log.warning(self._fail_reason)

    def wait_ready(self, timeout: float = READY_TIMEOUT_S) -> None:
        """阻塞等就绪（只在工作线程调用）。失败/超时抛 LLMError。"""
        if not self.running:
            self.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._ready.is_set():
                return
            if self._fail_reason or not self.running:
                raise LLMError(self._fail_reason or self._exit_reason())
            time.sleep(0.1)
        raise LLMError("本地模型仍在加载，请稍候再试")

    def _exit_reason(self) -> str:
        """进程已退出：把日志里最后一条错误带给用户，别只说"未在运行"。"""
        try:
            lines = SERVER_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        errs = [ln for ln in lines if " E " in ln[:16]]
        tail = errs[-1].split(" E ", 1)[-1].strip() if errs else ""
        return "本地模型服务已退出" + (f"：{tail[:160]}" if tail else f"，详见 {SERVER_LOG}")

    def backend_summary(self) -> str:
        try:
            return summarize_backend(SERVER_LOG.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return ""

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        proc, self._proc = self._proc, None
        self._ready.clear()
        if proc is None or proc.poll() is not None:
            return
        try:
            if _WINDOWS:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               creationflags=subprocess.CREATE_NO_WINDOW,
                               capture_output=True, timeout=10)
            else:
                proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


local_server = LocalServer()


class LocalClient(LLMClient):
    """走本地 llama-server 的 OpenAI 兼容客户端。

    stream_chat 先等服务就绪（在工作线程里阻塞），所以主线程拿客户端永远不卡；
    首次调用可能要等模型加载。翻译 prompt 用 Hy-MT 官方模板（见 translator）。
    """

    is_local = True

    def __init__(self, model_id: str):
        super().__init__(base_url=local_server.base_url(), api_key="local",
                         model=model_id, temperature=GEN_PARAMS["temperature"], timeout=180.0)
        self.extra_body = {k: v for k, v in GEN_PARAMS.items() if k != "temperature"}

    def stream_chat(self, messages):
        local_server.wait_ready()
        self.base_url = local_server.base_url()   # 重启后端口会变
        yield from super().stream_chat(messages)

    def test_connection(self) -> str:
        local_server.wait_ready()
        from .translator import build_local_messages

        reply = "".join(self.stream_chat(build_local_messages("Hello, world.", "zh-CN", "general")))
        if not reply.strip():
            raise LLMError("本地模型返回空")
        return f"本地模型可用（{local_server.backend_summary() or '运行中'}）：{reply.strip()[:30]}"


def local_client() -> LocalClient:
    """给 resolve_engine 用：未安装抛友好错误；已安装则确保服务在起（异步）。"""
    mid = installed_model()
    if mid is None:
        raise LLMError("本地模型未下载：设置 → 翻译模型 → 本地模型 → 下载并启用")
    if not local_server.running:
        local_server.start(mid)
    return LocalClient(mid)


def remove_all() -> None:
    """删掉运行时与全部模型（用户在设置里点"删除"）。"""
    local_server.stop()
    if LOCAL_DIR.exists():
        shutil.rmtree(LOCAL_DIR, ignore_errors=True)


def installed_summary() -> str:
    """状态栏文案（纯展示）。"""
    mid = installed_model()
    if mid is None:
        parts = []
        if runtime_ready():
            parts.append("运行时已就绪")
        for m, meta in MODELS.items():
            part = model_path(m).with_suffix(".gguf.part")
            if part.exists():
                try:
                    pct = int(part.stat().st_size * 100 / meta["size"])
                except OSError:
                    pct = 0
                parts.append(f"{meta['label'].split(' ·')[0]} 已下载 {pct}%（可续传）")
        return "未安装" + ("；" + "，".join(parts) if parts else "")
    state = "运行中 · " + (local_server.backend_summary() or "加载中") if local_server.running else "已安装（按需启动）"
    return f"{MODELS[mid]['label'].split('（')[0]} · {state}"


# ---------- 安装线程（设置页「下载并启用」） ----------

def _qt_installer_class():
    """延迟定义 QThread 子类：纯逻辑测试不必导入 Qt。"""
    from PySide6.QtCore import QThread, Signal

    class LocalModelInstaller(QThread):
        """下载运行时 + 模型 → 校验 → 拉起服务做一次加载验证。可取消、可续传。"""

        progress = Signal(str, int)      # 阶段文案, 百分比（-1 = 不定）
        finished_ok = Signal(str, str)   # 模型 id, 后端摘要（GPU · xxx / CPU）
        failed = Signal(str)

        def __init__(self, model_id: str, parent=None):
            super().__init__(parent)
            self._model_id = model_id
            self._cancel = False

        def cancel(self) -> None:
            self._cancel = True

        def run(self) -> None:
            try:
                self._run()
            except _Aborted:
                self.failed.emit("已取消（已下载部分会保留，下次续传）")
            except LLMError as e:
                self.failed.emit(str(e))
            except Exception as e:
                log.exception("本地模型安装失败")
                self.failed.emit(f"{e.__class__.__name__}: {e}")

        def _pct(self, stage: str):
            def cb(done: int, total: int) -> None:
                pct = int(done * 100 / total) if total else -1
                mb = done / 1048576
                self.progress.emit(f"{stage} {mb:.0f}MB" + (f" / {total / 1048576:.0f}MB" if total else ""), pct)
            return cb

        def _run(self) -> None:
            meta = MODELS[self._model_id]
            if not runtime_ready():
                name, sha = runtime_asset()
                self.progress.emit("下载运行时…", -1)
                archive = download_file(runtime_urls(), LOCAL_DIR / "downloads" / name, sha,
                                        progress=self._pct("下载运行时"),
                                        should_abort=lambda: self._cancel)
                self.progress.emit("解压运行时…", -1)
                extract_archive(archive, runtime_dir())
                archive.unlink(missing_ok=True)
                if not runtime_ready():
                    raise LLMError("运行时解压后找不到 llama-server")
            if not model_ready(self._model_id):
                self.progress.emit("下载模型…", 0)
                download_file(model_urls(self._model_id), model_path(self._model_id), meta["sha256"],
                              progress=self._pct("下载模型"),
                              should_abort=lambda: self._cancel,
                              expected_size=meta["size"])
            self.progress.emit("加载模型验证…", -1)
            local_server.start(self._model_id)
            local_server.wait_ready()
            self.finished_ok.emit(self._model_id, local_server.backend_summary())

    return LocalModelInstaller
