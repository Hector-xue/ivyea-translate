"""AutoGrowTextEdit：高度跟着内容走，框自身不滚（页面负责滚）。"""
import pytest
from PySide6.QtCore import QMimeData
from PySide6.QtWidgets import QApplication

from ivyea_translate.ui.widgets import AutoGrowTextEdit


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _settled(widget, app):
    for _ in range(4):
        app.processEvents()
    return widget.height()


def test_grows_with_content_and_never_scrolls_itself(app):
    w = AutoGrowTextEdit(min_height=100)
    w.resize(400, 100)
    w.show()
    short = _settled(w, app)
    assert short == 100, "空内容应保持最小高度"

    w.setPlainText("行\n" * 30)
    tall = _settled(w, app)
    assert tall > short + 100, f"内容变长高度应跟着长：{short} -> {tall}"
    assert not w.verticalScrollBar().isVisible(), "不封顶时框自身不该出滚动条"

    w.setPlainText("")
    assert _settled(w, app) == 100, "内容清空应回到最小高度"
    w.hide()


def test_max_height_caps_and_enables_own_scrollbar(app):
    # 弹窗那种固定容器：长到上限后由框自己滚
    w = AutoGrowTextEdit(min_height=60, max_height=200)
    w.resize(400, 60)
    w.show()
    w.setPlainText("行\n" * 60)
    assert _settled(w, app) == 200
    w.hide()


def test_paste_strips_rich_text(app):
    # QTextEdit 默认会把网页的字体/颜色一起粘进来，必须按纯文本收
    w = AutoGrowTextEdit()
    md = QMimeData()
    md.setHtml('<p style="color:red;font-size:28px"><b>红字</b></p>')
    md.setText("红字")
    w.insertFromMimeData(md)
    assert w.toPlainText() == "红字"
    assert "28px" not in w.toHtml()
