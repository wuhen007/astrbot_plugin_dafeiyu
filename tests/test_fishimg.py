"""PIL 画鱼测试（未装 Pillow 时自动跳过）。"""
import random
from pathlib import Path

import pytest

from game import GameConfig, Pond

pil = pytest.importorskip("PIL")

from render.fishimg import render_card_png, render_fish_png  # noqa: E402


class FakeClock:
    def __init__(self):
        self.now = 1_700_000_000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_pond():
    return Pond.create("t:1", config=GameConfig(), clock=FakeClock(), rng=random.Random(42))


def test_render_basic(tmp_path):
    pond = make_pond()
    pond.handle("u1", "小明", "投喂")
    out = tmp_path / "fish.png"
    path = render_fish_png(pond.state, str(out))
    assert Path(path).exists()
    assert Path(path).stat().st_size > 1000


def test_render_deterministic(tmp_path):
    pond = make_pond()
    pond.state.fish.satiety, pond.state.fish.mood = 40.0, 50.0
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    render_fish_png(pond.state, str(a))
    render_fish_png(pond.state, str(b))
    assert a.read_bytes() == b.read_bytes()


def test_render_all_states(tmp_path):
    pond = make_pond()
    fish = pond.state.fish
    # 脏水 + 饿 + 低心情（眼泪/垃圾/思考泡分支）
    fish.clean, fish.satiety, fish.mood, fish.energy = 10.0, 10.0, 10.0, 10.0
    render_fish_png(pond.state, str(tmp_path / "dirty.png"))
    # 高心情高活力（冲刺线分支）
    fish.clean, fish.satiety, fish.mood, fish.energy = 90.0, 90.0, 95.0, 95.0
    render_fish_png(pond.state, str(tmp_path / "happy.png"))
    # 毕业态（礼帽+彩带）
    fish.exp, fish.weight, fish.mood, fish.clean = 600, 90.0, 80.0, 70.0
    pond.state.users["u1"] = __import__("game.state", fromlist=["UserInfo"]).UserInfo(
        display_name="小明", intimacy=55, nick_set=True)
    pond.handle("u1", "小明", "出栏")
    render_fish_png(pond.state, str(tmp_path / "grad.png"))
    assert pond.state.status == "ended"


def test_decorated_scene_and_memorial_snapshot(tmp_path):
    from copy import deepcopy
    from render.fishimg import render_memorial_png
    pond = make_pond()
    plain = tmp_path / "plain.png"
    decor = tmp_path / "decor.png"
    render_fish_png(pond.state, str(plain))
    pond.state.decorations = {"水草角": {"a": 5}, "贝壳小屋": {"a": 8}, "月亮灯": {"a": 12}}
    render_fish_png(pond.state, str(decor))
    assert plain.read_bytes() != decor.read_bytes()
    pond.handle("u1", "很长的鱼友昵称" * 5, "逗鱼")
    pond._end("graduated", pond.clock())
    page = deepcopy(pond.state.archive[0])
    a, b = tmp_path / "page-a.png", tmp_path / "page-b.png"
    render_memorial_png(page, str(a))
    pond.handle("u1", "鱼友", "再养一条")
    render_memorial_png(page, str(b))
    assert a.read_bytes() == b.read_bytes()
    from PIL import Image
    assert Image.open(a).height > Image.open(decor).height
    render_memorial_png({"fish_name": "旧鱼"}, str(tmp_path / "old.png"))
