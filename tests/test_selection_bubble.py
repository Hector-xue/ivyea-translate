from ivyea_translate.selection_bubble import ClickInfo, classify_gesture


def _click(dx=(0, 0), dt=0.0, ux=(0, 0), ut=0.1):
    return ClickInfo(down_pos=dx, down_time=dt, up_pos=ux, up_time=ut)


def test_drag_selection_detected():
    info = _click(dx=(100, 100), dt=0.0, ux=(200, 104), ut=0.6)
    assert classify_gesture(info, None) == "drag"


def test_slow_drag_still_counts():
    info = _click(dx=(100, 100), dt=0.0, ux=(400, 100), ut=3.0)
    assert classify_gesture(info, None) == "drag"


def test_plain_click_ignored():
    info = _click(dx=(100, 100), dt=0.0, ux=(102, 101), ut=0.1)
    assert classify_gesture(info, None) is None


def test_double_click_detected():
    first = _click(dx=(100, 100), dt=0.0, ux=(101, 100), ut=0.1)
    second = _click(dx=(101, 100), dt=0.3, ux=(102, 101), ut=0.4)
    assert classify_gesture(second, first) == "dblclick"


def test_two_far_apart_clicks_not_double():
    first = _click(dx=(100, 100), dt=0.0, ux=(100, 100), ut=0.1)
    second = _click(dx=(300, 300), dt=0.3, ux=(300, 300), ut=0.4)
    assert classify_gesture(second, first) is None


def test_two_slow_clicks_not_double():
    first = _click(dx=(100, 100), dt=0.0, ux=(100, 100), ut=0.1)
    second = _click(dx=(100, 100), dt=1.5, ux=(101, 100), ut=1.6)
    assert classify_gesture(second, first) is None
