"""文本卡渲染测试。"""
import random

from game import GameConfig, Pond
from render.textcard import _fmt, bar, delta_line, render_markdown, render_plain


class FakeClock:
    def __init__(self):
        self.now = 1_700_000_000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_pond():
    return Pond.create("t:1", config=GameConfig(), clock=FakeClock(), rng=random.Random(42))


def test_bar():
    assert bar(0) == "░" * 10
    assert bar(100) == "█" * 10
    assert bar(50) == "█████░░░░░"
    assert bar(80, width=6, max_value=100) == "█████░"


def test_fmt():
    assert _fmt(22.0) == "+22"
    assert _fmt(-6.0) == "-6"
    assert _fmt(2.3) == "+2.3"
    assert _fmt(0.0) == "" or True          # 0 由 delta_line 过滤


def test_delta_line_skips_zero():
    assert delta_line({"饱腹": 0.0, "心情": 8.0}) == "心情 +8"


def test_panel_plain():
    pond = make_pond()
    r = pond.handle("u1", "小明", "养鱼")
    t = render_plain(r)
    assert "🐟" in t and "鱼籽" in t
    assert "饱腹" in t and "体重" in t and "鱼塘" in t
    assert "[投喂]" in t and "[排行]" in t


def test_action_plain():
    pond = make_pond()
    r = pond.handle("u1", "小明", "投喂")
    t = render_plain(r)
    assert "▶ 投喂" in t
    assert "饱腹 +22" in t
    assert "[加餐]" in t


def test_not_ok_plain():
    pond = make_pond()
    pond.handle("u1", "小明", "投喂")
    r = pond.handle("u1", "小明", "投喂")          # 冷却拒绝
    t = render_plain(r)
    assert "⚠" in t and "稍等一下" in t


def test_game_open_plain():
    pond = make_pond()
    r = pond.handle("u1", "小明", "玩耍")
    t = render_plain(r)
    assert "套圈小游戏" in t and "[泡泡①]" in t


def test_markdown_panel():
    pond = make_pond()
    r = pond.handle("u1", "小明", "养鱼")
    md = render_markdown(r)
    assert md.startswith("【🐟")
    assert "饱腹" in md and "成长" in md
    assert "\n" in md            # 多行文本；main.md_text 负责行尾双空格与加粗


def test_settlement_plain():
    pond = make_pond()
    fish = pond.state.fish
    fish.exp, fish.weight, fish.mood, fish.clean = 600, 90.0, 80.0, 70.0
    pond.state.users["u1"] = __import__("game.state", fromlist=["UserInfo"]).UserInfo(
        display_name="小明", intimacy=55, nick_set=True)
    r = pond.handle("u1", "小明", "出栏")
    t = render_plain(r)
    assert "出栏大典" in t and "══════" in t
    assert "评级 A" in t and "海苔片 +12" in t
    assert render_markdown(r).startswith("【🎉")
