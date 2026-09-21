import pytest

from ivyea_translate.config import LANGUAGES
from ivyea_translate.translator import (
    BODY_MARK,
    EMAIL_TONES,
    LANGUAGE_NAMES,
    SUBJECT_MARK,
    build_email_messages,
    parse_email_output,
)


def _system(messages):
    assert messages[0]["role"] == "system"
    return messages[0]["content"]


def test_email_messages_structure():
    msgs = build_email_messages("发货推迟三天，致歉", "en", "apologetic")
    assert len(msgs) == 2
    assert msgs[1]["content"] == "发货推迟三天，致歉"
    sys_prompt = _system(msgs)
    assert "English" in sys_prompt
    assert "apology" in sys_prompt
    assert SUBJECT_MARK in sys_prompt and BODY_MARK in sys_prompt
    assert "subject line" in sys_prompt


@pytest.mark.parametrize("tone", list(EMAIL_TONES))
def test_all_tones_have_rules(tone):
    label, rule = EMAIL_TONES[tone]
    assert label and rule
    assert rule in _system(build_email_messages("hi", "en", tone))


@pytest.mark.parametrize("code,_label", LANGUAGES)
def test_email_supports_all_languages(code, _label):
    sys_prompt = _system(build_email_messages("hi", code, "business"))
    assert LANGUAGE_NAMES[code] in sys_prompt


def test_unknown_tone_falls_back_to_business():
    sys_prompt = _system(build_email_messages("hi", "en", "nonsense"))
    assert EMAIL_TONES["business"][1] in sys_prompt


def test_parse_standard_output():
    subject, body = parse_email_output(
        "【主题】Delivery delayed by three days\n【正文】\nDear customer,\n\nWe are sorry...\n\nBest regards"
    )
    assert subject == "Delivery delayed by three days"
    assert body.startswith("Dear customer,")
    assert body.endswith("Best regards")


def test_parse_missing_body_marker():
    subject, body = parse_email_output("【主题】Hello\nDear team,\nFYI.")
    assert subject == "Hello"
    assert body == "Dear team,\nFYI."


def test_parse_subject_prefix_fallback():
    subject, body = parse_email_output("Subject: Weekly report\nHi all,\n...")
    assert subject == "Weekly report"
    assert body.startswith("Hi all,")


def test_parse_no_markers_degrades_gracefully():
    subject, body = parse_email_output("Dear team,\njust the body.")
    assert subject == ""
    assert body == "Dear team,\njust the body."


# ---------- 反向写作台（compose）----------

from ivyea_translate.translator import (
    COMPOSE_SCENARIOS,
    build_compose_messages,
    parse_compose_output,
)


def test_email_scenario_has_subject_markers():
    sys_prompt = _system(build_compose_messages("发货推迟", "en", "email", "business"))
    assert SUBJECT_MARK in sys_prompt and BODY_MARK in sys_prompt


@pytest.mark.parametrize("scen", [s for s in COMPOSE_SCENARIOS if s != "email"])
def test_nonemail_scenarios_no_subject(scen):
    sys_prompt = _system(build_compose_messages("hi", "en", scen, "concise"))
    assert SUBJECT_MARK not in sys_prompt
    assert "ONLY the rewritten text" in sys_prompt


def test_all_scenarios_have_metadata():
    for code, (label, desc, want_subject) in COMPOSE_SCENARIOS.items():
        assert label and desc and isinstance(want_subject, bool)


def test_scenario_description_injected():
    sys_prompt = _system(build_compose_messages("x", "en", "comment", "concise"))
    assert "code review" in sys_prompt.lower()


def test_parse_compose_email_splits_subject():
    subject, body = parse_compose_output("【主题】Hello\n【正文】\nDear team,\nFYI.", "email")
    assert subject == "Hello"
    assert body.startswith("Dear team,")


def test_parse_compose_nonemail_is_whole_body():
    subject, body = parse_compose_output("Just a chat message, no subject.", "message")
    assert subject == ""
    assert body == "Just a chat message, no subject."


def test_build_email_messages_still_works():
    # 旧接口保持可用
    sys_prompt = _system(build_email_messages("发货推迟", "en", "business"))
    assert SUBJECT_MARK in sys_prompt


# ---------- 详解模式 ----------

from ivyea_translate.translator import build_explain_messages


def test_explain_messages_structure():
    msgs = build_explain_messages("It's a piece of cake.", "小菜一碟。", "zh-CN")
    assert len(msgs) == 2
    sys_prompt = msgs[0]["content"]
    assert "Simplified Chinese" in sys_prompt        # 用母语讲解
    assert "pronunciation" in sys_prompt.lower()
    assert "It's a piece of cake." in msgs[1]["content"]
    assert "小菜一碟。" in msgs[1]["content"]


def test_explain_language_varies():
    sys_prompt = build_explain_messages("x", "y", "ja")[0]["content"]
    assert "Japanese" in sys_prompt


# ---------- 回译校对覆盖主题（UI 接线层） ----------

def test_backtranslation_includes_subject(qapp, tmp_path):
    """邮件场景生成的主题也是生成物，回译校对必须一并覆盖，不能只校正文。"""
    from ivyea_translate.config import Config
    from ivyea_translate.ui.main_window import MainWindow

    win = MainWindow(Config(tmp_path / "c.json"))
    got = []
    win._run_backtranslation = got.append
    win._email_done("草稿", "【主题】Order Update\n【正文】\nHello body.", "email")
    assert got and got[0].startswith("Order Update\n\n")
    assert "Hello body." in got[0]

    got.clear()
    win._email_done("草稿", "Just a message.", "message")  # 无主题场景只回译正文
    assert got == ["Just a message."]
    win.really_quit = True
    win.close()


def test_compose_under_free_engine_translates_directly(qapp, tmp_path, monkeypatch):
    """免费引擎模式下写作助手不能锁死：降级为直译（不带改写 prompt），并明说。"""
    from ivyea_translate.config import Config
    from ivyea_translate.ui import main_window as mw

    cfg = Config(tmp_path / "c.json")
    cfg.set("translate.engine", "free")
    win = mw.MainWindow(cfg)
    made = {}

    class FakeWorker:
        def __init__(self, client, text, lang, style, parent=None, messages=None):
            made.update(client=client, text=text, lang=lang, messages=messages)
            self.chunk = self.finished_ok = self.failed = _Sig()
        def start(self):
            pass
        def isRunning(self):
            return False

    class _Sig:
        def connect(self, *_):
            pass

    monkeypatch.setattr(mw, "TranslateWorker", FakeWorker)
    win.email_source.setPlainText("告诉客户发货推迟三天")
    win._on_email_clicked()
    assert getattr(made["client"], "is_free", False) is True
    assert made["messages"] is None                 # 直译，不走改写 prompt
    assert "免费翻译引擎" in win.email_hint.text()
    # 直译结果整段进正文、无主题
    win._run_backtranslation = lambda *_: None
    win._email_done("草稿", "Tell the customer shipping is delayed by three days.", "email")
    assert win.email_subject.text() == ""
    assert win.email_body.toPlainText().startswith("Tell the customer")
    win.really_quit = True
    win.close()


def test_compose_with_llm_still_uses_rewrite_prompt(qapp, tmp_path, monkeypatch):
    from ivyea_translate.config import Config
    from ivyea_translate.ui import main_window as mw

    cfg = Config(tmp_path / "c.json")
    cfg.set("provider.api_key", "sk-x")
    cfg.set("provider.base_url", "https://api.example.com/v1")
    cfg.set("provider.model", "m")
    win = mw.MainWindow(cfg)
    made = {}

    class _Sig:
        def connect(self, *_):
            pass

    class FakeWorker:
        def __init__(self, client, text, lang, style, parent=None, messages=None):
            made.update(messages=messages)
            self.chunk = self.finished_ok = self.failed = _Sig()
        def start(self):
            pass
        def isRunning(self):
            return False

    monkeypatch.setattr(mw, "TranslateWorker", FakeWorker)
    win.email_source.setPlainText("草稿")
    win._on_email_clicked()
    assert made["messages"] and made["messages"][0]["role"] == "system"
    win.really_quit = True
    win.close()
