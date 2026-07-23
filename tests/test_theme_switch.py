"""多主题换肤：令牌完整、QSS 可生成、背景层能画、动效开关真的生效。"""
from pathlib import Path

import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QPixmap

from ivyea_translate.ui import motion as motion_mod
from ivyea_translate.ui import theme
from ivyea_translate.ui.backdrop import Backdrop
from ivyea_translate.ui.hero import HeroBanner

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "themes"

# 换肤会改全局令牌，用完必须还原，否则污染同进程里的其他测试
@pytest.fixture(autouse=True)
def restore_theme():
    yield
    theme.apply(theme.DEFAULT_THEME)


@pytest.mark.parametrize("key", theme.theme_keys())
def test_tokens_and_qss(key):
    assert theme.apply(key) == key
    assert theme.current() == key
    # 老代码到处直接读这些模块级常量，少一个就是运行时 AttributeError
    for name in ("ACCENT", "CARD_BG", "CARD_BORDER", "FIELD_BG", "POPUP_BG",
                 "TEXT_PRIMARY", "TEXT_SECONDARY", "TEXT_MUTED", "DANGER", "OK",
                 "SHADOW_RGB", "SHADOW_ALPHA", "SHELL_GRADIENT", "GLASS_CARD"):
        assert getattr(theme, name, None) is not None, name
    qss = theme.app_qss()
    assert theme.ACCENT in qss and theme.CARD_BORDER in qss
    assert "{" not in qss.split("*", 1)[0]      # f-string 没有漏花括号
    assert len(theme.SHADOW_RGB) == 3


def test_unknown_theme_falls_back():
    assert theme.apply("nope") == theme.DEFAULT_THEME


@pytest.mark.parametrize("key", theme.theme_keys())
def test_theme_assets_present(key):
    if not theme.spec(key).get("photo", True):
        # 纯色主题本来就没有照片：确认它也确实没引用照片资源
        assert not theme.spec(key)["motion"]
        return
    for name in ("bg.jpg", "thumb.jpg"):
        assert (ASSETS / key / name).exists(), f"{key}/{name} 缺失"
    assert theme.theme_asset("bg.jpg", key)


@pytest.mark.parametrize("key", theme.theme_keys())
def test_backdrop_paints_every_theme(qapp, key):
    """六套主题的背景层都要能画出内容（缺资源/引擎报错会得到一张空图）。"""
    from PySide6.QtWidgets import QWidget

    theme.apply(key)
    host = QWidget()
    host.resize(420, 300)
    bd = Backdrop(host, motion_enabled=True)
    bd.resize(420, 300)
    bd._engine.resize(420, 300) if bd._engine else None
    for _ in range(3):
        bd._tick()
    pm = bd.grab()
    assert pm.size() == QSize(420, 300)
    if not theme.spec(key).get("photo", True):
        return          # 纯色主题的底色由 Shell 的 QSS 渐变画，背景层本来就不画东西
    img = pm.toImage()
    colors = {img.pixel(x, y) for x in range(0, 420, 37) for y in range(0, 300, 29)}
    assert len(colors) > 3, f"{key} 背景层几乎是纯色，八成没画出来"


def test_motion_toggle_stops_timer(qapp):
    from PySide6.QtWidgets import QWidget

    theme.apply("sakura")
    host = QWidget()
    host.resize(300, 200)
    host.show()
    bd = Backdrop(host, motion_enabled=True)
    bd.show()
    assert bd._timer.isActive()
    bd.set_motion(False)
    assert not bd._timer.isActive()
    bd.set_motion(True)
    assert bd._timer.isActive()
    bd.hide()                       # 藏起来必须停表：本软件常年挂托盘
    assert not bd._timer.isActive()
    host.close()


def test_hero_reload_switches_theme(qapp):
    theme.apply("ivy")
    hero = HeroBanner(motion_enabled=False)
    hero.resize(600, 96)
    first = theme.spec()["slogan"]
    theme.apply("cyber")
    hero.reload()
    assert theme.spec()["slogan"] != first
    pm = hero.grab()
    assert not pm.isNull()


def test_sprites_load_for_every_motion(qapp):
    """精灵缺失时引擎必须降级而不是崩——但正常仓库里应当都在。"""
    motion_mod.clear_cache()
    for key in theme.theme_keys():
        theme.apply(key)
        eng = motion_mod.build(theme.spec()["motion"])
        if not theme.spec()["motion"]:
            assert eng is None      # 纯色主题不跑动效
            continue
        assert eng is not None
        eng.resize(320, 240)
        pm = QPixmap(320, 240)
        pm.fill()
        from PySide6.QtGui import QPainter

        p = QPainter(pm)
        eng.step(0.05, 320, 240)
        eng.draw(p, 320, 240)
        p.end()


@pytest.mark.parametrize("key", [k for k in theme.theme_keys()
                                 if not theme.spec(k).get("photo", True)])
def test_solid_theme_never_starts_timer(qapp, key):
    """纯色主题没有动效：定时器一次都不该起（否则就是每秒 30 次重画同一张图）。"""
    from PySide6.QtWidgets import QWidget

    theme.apply(key)
    host = QWidget()
    host.resize(320, 240)
    host.show()
    bd = Backdrop(host, motion_enabled=True)
    bd.show()
    assert bd._engine is None
    assert not bd._timer.isActive()
    bd.set_motion(True)              # 用户就算把动效开着，纯色主题也不该起表
    assert not bd._timer.isActive()
    host.close()


def test_switch_between_solid_and_photo_theme_syncs_timer(qapp):
    from PySide6.QtWidgets import QWidget

    theme.apply("mint")
    host = QWidget()
    host.resize(320, 240)
    host.show()
    bd = Backdrop(host, motion_enabled=True)
    bd.show()
    assert not bd._timer.isActive()
    theme.apply("ivy")
    bd.reload()
    assert bd._timer.isActive(), "换回有动效的主题后要把表重新起起来"
    theme.apply("midnight")
    bd.reload()
    assert not bd._timer.isActive(), "换到纯色主题要停表"
    host.close()


def test_card_opacity_override():
    """前景透明度是全局设置：换主题要保留，复位要回到各主题默认值。"""
    theme.apply("ivy")
    default = theme.card_opacity()
    assert "rgba" in theme.CARD_BG

    theme.set_card_opacity(0.62)
    assert theme.card_opacity() == 0.62
    assert "0.62" in theme.CARD_BG
    assert theme.ACCENT in theme.app_qss()

    theme.apply("midnight")           # 换主题不该把用户调的值冲掉
    assert theme.card_opacity() == 0.62
    assert "0.62" in theme.CARD_BG

    theme.set_card_opacity(None)      # 复位回主题默认
    theme.apply("ivy")
    assert theme.card_opacity() == default

    theme.set_card_opacity(0.1)       # 越界要夹住，别让卡片透没了
    assert theme.card_opacity() == 0.55
    theme.set_card_opacity(None)


def test_opacity_does_not_leak_into_popup_or_fields():
    """弹窗常年浮在别人家窗口上、输入框要和卡片分层：都不跟着透。"""
    theme.apply("ivy")
    popup_before, field_before = theme.POPUP_BG, theme.FIELD_BG
    theme.set_card_opacity(0.55)
    assert theme.POPUP_BG == popup_before
    assert theme.FIELD_BG == field_before
    theme.set_card_opacity(None)
