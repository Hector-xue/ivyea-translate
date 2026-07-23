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
    theme.apply("ivy")
    hero = HeroBanner(motion_enabled=False)
    hero.resize(760, 96)

    hero._quote = ("辞达而已矣。", "《论语·卫灵公》")
    short_px, short_lines = hero._layout_quote(640)
    hero._quote = ("叹隙中驹，石中火，梦中身。几时归去，作个闲人。"
                   "对一张琴，一壶酒，一溪云。", "苏轼《行香子·述怀》")
    long_px, long_lines = hero._layout_quote(640)

    assert short_px > long_px, "短句该用更大的字号"
    assert len(short_lines) == 1
    assert "".join(long_lines) == hero._quote[0], "折行不能吞字"
    assert len(long_lines) <= 3


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
