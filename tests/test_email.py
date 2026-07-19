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
