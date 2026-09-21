"""全链路计时：阶段顺序、同名只记首个、汇总格式。"""
from ivyea_translate.perf import Trace


def test_marks_keep_first_occurrence_and_order():
    tr = Trace("截图翻译")
    tr.mark("检测")
    tr.mark("首段识别")
    tr.mark("首段识别")   # 第二段识完也叫"首段识别"？不：同名只记第一次
    assert [l for l, _ in tr._marks] == ["检测", "首段识别"]


def test_summary_is_incremental_and_finish_is_idempotent():
    tr = Trace("截图翻译")
    tr.mark("框选")
    tr.mark("检测")
    text = tr.finish()
    assert text.startswith("截图翻译耗时 ")
    assert "框选 0." in text and "检测 +0." in text and "完成 +0." in text
    assert tr.finish() == ""          # 第二次不再输出
    assert tr.elapsed("检测") is not None and tr.elapsed("没有") is None
