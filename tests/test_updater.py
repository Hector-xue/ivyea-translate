import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from ivyea_translate.updater import is_newer, normalize_feed, parse_version


def test_parse_version():
    assert parse_version("0.3.0") == (0, 3, 0)
    assert parse_version("v1.2.10") == (1, 2, 10)
    with pytest.raises(ValueError):
        parse_version("abc")
    with pytest.raises(ValueError):
        parse_version("")


def test_is_newer():
    assert is_newer("0.3.0", "0.2.0")
    assert is_newer("0.10.0", "0.9.9")   # 数值比较而非字符串
    assert not is_newer("0.2.0", "0.2.0")
    assert not is_newer("0.1.9", "0.2.0")
    assert not is_newer("垃圾数据", "0.2.0")  # 非法远端版本不触发更新


def test_normalize_feed_valid():
    feed = normalize_feed({
        "version": "v0.3.0",
        "setup_url": "https://translate.ivyea.com/download/IvyeaTranslate-Setup.exe",
        "notes": "修复若干问题",
    })
    assert feed["version"] == "0.3.0"
    assert feed["setup_url"].startswith("https://")
    assert feed["notes"] == "修复若干问题"


def test_normalize_feed_rejects_bad():
    assert normalize_feed(None) is None
    assert normalize_feed([]) is None
    assert normalize_feed({"version": "0.3.0"}) is None                       # 缺 setup_url
    assert normalize_feed({"version": "0.3.0", "setup_url": "http://x/a"}) is None  # 非 https
    assert normalize_feed({"version": "bad", "setup_url": "https://x/a"}) is None


class _FeedHandler(BaseHTTPRequestHandler):
    payload = {}

    def do_GET(self):
        body = json.dumps(self.payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _spin(qapp, cond, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_checker_detects_new_version(qapp):
    from ivyea_translate.updater import UpdateChecker

    _FeedHandler.payload = {
        "version": "99.0.0",
        "setup_url": "https://translate.ivyea.com/download/IvyeaTranslate-Setup.exe",
    }
    srv = HTTPServer(("127.0.0.1", 0), _FeedHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        got = {}
        checker = UpdateChecker(f"http://127.0.0.1:{srv.server_address[1]}/version.json")
        checker.update_available.connect(lambda feed: got.update(feed))
        checker.no_update.connect(lambda: got.update(no=True))
        checker.start()
        assert _spin(qapp, lambda: got)
        assert got.get("version") == "99.0.0"
    finally:
        srv.shutdown()


def test_checker_no_update_for_old_version(qapp):
    from ivyea_translate.updater import UpdateChecker

    _FeedHandler.payload = {
        "version": "0.0.1",
        "setup_url": "https://translate.ivyea.com/download/IvyeaTranslate-Setup.exe",
    }
    srv = HTTPServer(("127.0.0.1", 0), _FeedHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        got = {}
        checker = UpdateChecker(f"http://127.0.0.1:{srv.server_address[1]}/version.json")
        checker.update_available.connect(lambda feed: got.update(feed))
        checker.no_update.connect(lambda: got.update(no=True))
        checker.start()
        assert _spin(qapp, lambda: got)
        assert got.get("no") is True
    finally:
        srv.shutdown()
