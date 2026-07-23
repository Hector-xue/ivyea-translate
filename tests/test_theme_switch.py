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
        assert eng is not None
        eng.resize(320, 240)
        pm = QPixmap(320, 240)
        pm.fill()
        from PySide6.QtGui import QPainter

        p = QPainter(pm)
        eng.step(0.05, 320, 240)
        eng.draw(p, 320, 240)
        p.end()
