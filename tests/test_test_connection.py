"""设置页「测试连接」回归。

背景：结果曾用 QTimer.singleShot(0,...) 从后台线程回传——Qt 定时器不能在
非 Qt 线程启动，回调永不执行，界面永远停在"测试中…"。现在走 Signal。
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


class _OkHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunk = {"choices": [{"delta": {"content": "pong"}}]}
        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *a):
        pass


def _spin_until(qapp, cond, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_test_connection_reports_back(qapp, tmp_path):
    from ivyea_translate.config import Config
    from ivyea_translate.ui.main_window import MainWindow

    srv = HTTPServer(("127.0.0.1", 0), _OkHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        cfg = Config(tmp_path / "config.json")
        win = MainWindow(cfg)
        win.base_url_edit.setText(f"http://127.0.0.1:{srv.server_address[1]}/v1")
        win.api_key_edit.setText("sk-test")
        win.model_edit.setText("mock")

        win._on_test_connection()
        assert win.test_result.text() == "测试中…"

        ok = _spin_until(qapp, lambda: win.test_result.text() != "测试中…")
        assert ok, "测试结果没有回传（回归：QTimer 跨线程问题）"
        assert "连通正常" in win.test_result.text()
        assert win.test_btn.isEnabled()
    finally:
        srv.shutdown()


def test_test_connection_reports_failure(qapp, tmp_path):
    from ivyea_translate.config import Config
    from ivyea_translate.ui.main_window import MainWindow

    cfg = Config(tmp_path / "config.json")
    win = MainWindow(cfg)
    win.base_url_edit.setText("http://127.0.0.1:1/v1")  # 必然连不上
    win.api_key_edit.setText("sk-test")
    win.model_edit.setText("mock")

    win._on_test_connection()
    ok = _spin_until(qapp, lambda: win.test_result.text() != "测试中…", timeout=15.0)
    assert ok
    assert "连通正常" not in win.test_result.text()
