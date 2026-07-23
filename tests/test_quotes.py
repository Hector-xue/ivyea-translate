"""横幅名句：数据本身、洗牌发牌、以及排版/缓存行为。"""
import pytest

from ivyea_translate import quotes as quotes_mod
from ivyea_translate.ui import theme
from ivyea_translate.ui.hero import HeroBanner


@pytest.fixture(autouse=True)
def restore_theme():
    yield
    theme.apply(theme.DEFAULT_THEME)


def test_quote_data_sane():
    assert len(quotes_mod.QUOTES) >= 100, "说好的上百条"
    texts = [t for t, _ in quotes_mod.QUOTES]
    assert len(set(texts)) == len(texts), "有重复条目"
    for text, src in quotes_mod.QUOTES:
        assert text.strip() and src.strip(), f"空条目：{text!r} / {src!r}"
        assert len(text) <= 60, f"太长了横幅塞不下：{text}"
        # 出处必须是人名或书名/片名，不能是空泛的"佚名"
        assert "佚名" not in src and "网络" not in src, f"出处不明：{src}"


def test_deck_no_repeat_within_a_cycle():
    """纯随机会连着抽到同一句，用户一眼就看出来轮播坏了。"""
    deck = quotes_mod.Deck(seed=42)
    drawn = [deck.draw() for _ in range(len(quotes_mod.QUOTES))]
    assert len(set(drawn)) == len(quotes_mod.QUOTES)
    # 跨轮之间也不该紧挨着重复出现同一条（洗牌后第一张碰巧相同的概率极低，
    # 这里只验证发牌还能继续，不做概率断言）
    assert deck.draw() in quotes_mod.QUOTES


def test_short_quote_gets_bigger_font_than_long_one(qapp):
    """同样的可用宽度下，长句必须让位——降字号或者折行。

    这里的宽度不能写死：CI 机器上没装中文字体，汉字全被量成同一个窄框，
    写死宽度会让长句在最大号下也"放得下"，测试就白测了。改成按短句在最大号下
    的实际宽度来定，无论字体怎样，长句都塞不进这个宽度。
    """
    from PySide6.QtGui import QFontMetricsF

    theme.apply("ivy")
    hero = HeroBanner(motion_enabled=False)
    hero.resize(760, 96)

    short = ("辞达而已矣。", "《论语·卫灵公》")
    long_ = ("叹隙中驹，石中火，梦中身。几时归去，作个闲人。"
             "对一张琴，一壶酒，一溪云。", "苏轼《行香子·述怀》")
    from ivyea_translate.ui import hero as hero_mod

    biggest = hero_mod.SIZE_LADDER[0][0]
    fm = QFontMetricsF(hero._quote_font(biggest))
    width = fm.horizontalAdvance(short[0]) + 8      # 刚好够短句排一行

    hero._quote = short
    short_px, short_lines = hero._layout_quote(width)
    hero._quote = long_
    long_px, long_lines = hero._layout_quote(width)

    assert short_px == biggest and len(short_lines) == 1, "短句该用最大号排一行"
    assert (long_px < short_px) or (len(long_lines) > 1), "长句得降号或折行"
    assert "".join(long_lines) == long_[0], "折行不能吞字"
    assert len(long_lines) <= 4


def test_next_quote_changes_content_and_invalidates_layer(qapp):
    theme.apply("mint")
    hero = HeroBanner(motion_enabled=False)   # 关动效=直接换，不走淡入淡出
    hero.resize(760, 96)
    hero._ensure_layer(760, 96)
    assert hero._layer is not None

    before = hero._quote
    hero.next_quote()
    assert hero._quote != before
    assert hero._layer is None, "换了句还用旧的缓存图，画面就不会变"


def test_rotation_timer_follows_visibility(qapp):
    theme.apply("ivy")
    hero = HeroBanner(motion_enabled=True)
    hero.resize(760, 96)
    hero.show()
    assert hero._rotate.isActive()
    hero.hide()
    assert not hero._rotate.isActive(), "藏起来还在轮播就是白烧 CPU"


def test_layer_cache_reused_when_nothing_changed(qapp):
    theme.apply("starfield")
    hero = HeroBanner(motion_enabled=False)
    hero.resize(800, 96)
    first = hero._ensure_layer(800, 96)
    again = hero._ensure_layer(800, 96)
    assert first is again, "没变化就该复用缓存，否则横幅会跟着背景每帧重画"
