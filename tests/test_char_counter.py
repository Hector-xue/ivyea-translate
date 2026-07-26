"""主窗口四处字符统计：翻译页输入/译文、写作页输入/正文。

重点不是"数字对不对"（那是 test_textstats 的事），而是**联动**：
setPlainText / 清空 / 历史回填都得自己刷新，长文本预警要按真实配置来，
换肤之后内联的预警色不能掉。
"""
from ivyea_translate.free_engine import MAX_CHUNK


def _make_window(qapp, tmp_path):
    from ivyea_translate.config import Config
    from ivyea_translate.ui.main_window import MainWindow

    cfg = Config(tmp_path / "config.json")
    return MainWindow(cfg)


def test_all_four_counters_exist(qapp, tmp_path):
    win = _make_window(qapp, tmp_path)
    for name in ("source_count", "result_count", "email_source_count", "email_body_count"):
        assert hasattr(win, name), f"缺了 {name}"
    win.deleteLater()


def test_source_and_result_counts_follow_text(qapp, tmp_path):
    win = _make_window(qapp, tmp_path)
    win.source_edit.setPlainText("hello world")
    assert win.source_count.text() == "11 字符"
    win.result_view.setPlainText("你好世界")
    assert win.result_count.text() == "4 字符"
    win.deleteLater()


def test_email_counts_follow_text(qapp, tmp_path):
    win = _make_window(qapp, tmp_path)
    win.email_source.setPlainText("告诉客户发货推迟")
    assert win.email_source_count.text() == "8 字符"
    win.email_body.setPlainText("Dear customer")
    assert win.email_body_count.text() == "13 字符"
    win.deleteLater()


def test_clearing_empties_the_counter(qapp, tmp_path):
    """空框下不显示"0 字符"——占位状态多一行噪音。"""
    win = _make_window(qapp, tmp_path)
    win.source_edit.setPlainText("abc")
    win._clear_translate()
    assert win.source_count.text() == ""
    assert win.result_count.text() == ""
    win.deleteLater()


def test_streaming_append_updates_count(qapp, tmp_path):
    """流式回填走 insertPlainText，同样得让计数跟着跳。"""
    win = _make_window(qapp, tmp_path)
    win._append_result("Hel")
    win._append_result("lo")
    assert win.result_count.text() == "5 字符"
    win.deleteLater()


def test_tooltip_carries_full_breakdown(qapp, tmp_path):
    win = _make_window(qapp, tmp_path)
    win.source_edit.setPlainText("翻译 hello")
    tip = win.source_count.toolTip()
    assert "不含空格" in tip and "词" in tip and "行" in tip
    win.deleteLater()


# ---------- 长文本预警（只在翻译页输入侧，且只报真实存在的限制） ----------

def test_no_warning_for_short_text(qapp, tmp_path):
    win = _make_window(qapp, tmp_path)
    win.source_edit.setPlainText("hello")
    assert win.source_count.styleSheet() == ""
    win.deleteLater()


def test_free_engine_chunk_hint(qapp, tmp_path):
    """免费引擎超过 MAX_CHUNK 会切段串行请求——不是错误，用提示色。"""
    win = _make_window(qapp, tmp_path)
    win.cfg.set("translate.engine", "free")
    win.source_edit.setPlainText("a" * (MAX_CHUNK + 10))
    assert "分" in win.source_count.toolTip() and "段" in win.source_count.toolTip()
    assert win.source_count.styleSheet() != ""
    win.deleteLater()


def test_no_chunk_hint_when_using_llm(qapp, tmp_path):
    """大模型链路没有字符上限，报切段是误导。"""
    win = _make_window(qapp, tmp_path)
    win.cfg.set("translate.engine", "llm")
    win.source_edit.setPlainText("a" * (MAX_CHUNK + 10))
    assert "段" not in win.source_count.toolTip()
    win.deleteLater()


def test_hard_limit_warning_mentions_double_copy(qapp, tmp_path):
    """double_copy.max_chars 是全项目唯一的硬限，超了划词静默不翻，必须说清。"""
    win = _make_window(qapp, tmp_path)
    limit = int(win.cfg.get("double_copy.max_chars", 3000))
    win.source_edit.setPlainText("a" * (limit + 1))
    tip = win.source_count.toolTip()
    assert str(limit) in tip and "划词" in tip
    assert "仍会完整翻译" in tip   # 别让用户以为主窗口也翻不了
    win.deleteLater()


def test_email_page_never_warns(qapp, tmp_path):
    """写作页走大模型，不受免费引擎切段和划词上限影响。"""
    win = _make_window(qapp, tmp_path)
    win.cfg.set("translate.engine", "free")
    win.email_source.setPlainText("a" * 5000)
    assert win.email_source_count.styleSheet() == ""
    assert "段" not in win.email_source_count.toolTip()
    win.deleteLater()


def test_warning_color_survives_theme_switch(qapp, tmp_path):
    """预警色是内联样式，会盖过全局 QSS；换肤后必须被 restyle 补回来。"""
    from ivyea_translate.ui import theme

    win = _make_window(qapp, tmp_path)
    win.cfg.set("translate.engine", "free")
    win.source_edit.setPlainText("a" * (MAX_CHUNK + 10))
    other = [k for k in theme.theme_keys() if k != theme.current()][0]
    try:
        theme.apply(other)
        win.restyle()
        assert theme.ACCENT in win.source_count.styleSheet()
    finally:
        theme.apply(theme.DEFAULT_THEME)  # 换肤是全局令牌，别污染同进程其他用例
    win.deleteLater()
