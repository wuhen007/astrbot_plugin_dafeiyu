"""性格推导、每周运动会及跨轮回限制。"""
import random
from copy import deepcopy
from datetime import datetime

import pytest

from game import GameConfig, Pond, PondState
from game.engine import memorial_lines


@pytest.fixture
def pond():
    cfg = GameConfig(daily_bonus_snacks=0, decay_mood=0, decay_satiety=0, decay_clean=0)
    cfg.rescue.chance = 0
    now = [datetime(2026, 11, 18, 12).timestamp()]
    p = Pond.create("sports:test", cfg, lambda: now[0], random.Random(42))
    p.now = now
    return p


@pytest.mark.parametrize("category,expected", [("feed", "贪吃"), ("pet", "黏人"),
    ("clean", "爱干净"), ("adventure", "好奇"), ("play", "好奇")])
def test_personality_threshold_and_live_change(pond, category, expected):
    pond.handle("a", "甲", "养鱼")
    pond.state.round_stats["a"] = {category: 4}
    assert pond._personality() is None
    pond.state.round_stats["a"][category] = 5
    assert pond._personality() == expected
    assert expected in " ".join(pond.handle("a", "甲", "养鱼").lines)
    pond.state.round_stats["b"] = {"pet": 10}
    assert pond._personality() == "黏人"


def test_personality_only_effective_actions_and_snapshot(pond):
    for _ in range(5):
        pond.state.fish.cooldowns.clear()
        for user in pond.state.users.values():
            user.cooldowns.clear()
        pond.state.fish.mood = 40
        pond.handle("a", "甲", "逗鱼")
    assert pond._personality() == "黏人"
    before = deepcopy(pond.state.fish.to_dict())
    pond.handle("a", "甲", "性格")
    assert pond.state.fish.to_dict() == before
    pond.state.fish.cooldowns.clear()
    for user in pond.state.users.values():
        user.cooldowns.clear()
    pond.state.fish.satiety = 100
    pond.handle("a", "甲", "投喂")
    assert pond.state.round_stats["a"].get("feed", 0) == 0
    pond.handle("a", "甲", "奇遇")
    assert "黏人" in " ".join(pond.handle("a", "甲", "奇遇").lines)
    pond._end("graduated", pond.clock())
    page = deepcopy(pond.state.archive[-1])
    assert "本轮性格：黏人" in memorial_lines(page)
    pond.handle("a", "甲", "再养一条")
    assert pond._personality() is None
    assert pond.state.archive[-1] == page


def enter(pond, uid="a", project="冲刺", strategy="爆发"):
    menu = pond.handle(uid, uid, "运动会", project)
    button = next(b for b in menu.buttons if b.label == strategy)
    return pond.handle(uid, uid, "运动会", button.command.split(maxsplit=2)[2])


def test_sports_select_strategy_score_once_and_reload(pond):
    pond.state.fish.energy = 100
    before = deepcopy(pond.state.fish.to_dict())
    menu = pond.handle("a", "甲", "运动会", "冲刺")
    assert len(menu.buttons) == 3 and pond.state.users["a"].sports_badges == 0
    args = menu.buttons[-1].command.split(maxsplit=2)[2]
    assert pond.handle("a", "甲", "运动会", args).ok
    entry = pond.state.sports_week["entries"]["a"]
    assert 90 <= entry["score"] <= 100
    assert pond.state.fish.to_dict() == before
    assert pond.state.users["a"].sports_badges == 1
    assert not pond.handle("a", "甲", "运动会", args).ok
    assert pond.state.users["a"].sports_badges == 1
    loaded = PondState.from_dict(pond.state.to_dict())
    assert loaded.to_dict() == pond.state.to_dict()


def test_sports_matching_strategy_and_bad_arguments(pond):
    assert not pond.handle("a", "甲", "运动会", "错误项目").ok
    week = pond.state.sports_week["week"]
    assert not pond.handle("a", "甲", "运动会", f"冲刺@作弊@{week}").ok
    assert not pond.handle("a", "甲", "运动会", f"冲刺@爆发@{week}@{week}").ok
    pond.state.fish.energy = 100
    pond.rng = random.Random(17)
    enter(pond, "a", strategy="稳健")
    pond.rng = random.Random(17)
    enter(pond, "b", strategy="爆发")
    e = pond.state.sports_week["entries"]
    assert e["b"]["score"] - e["a"]["score"] == 20


def test_sports_cheers_week_boundary_restart_old_button(pond):
    menu = pond.handle("a", "甲", "运动会", "冲刺")
    old = menu.buttons[0].command.split(maxsplit=2)[2]
    enter(pond)
    assert pond.handle("a", "a", "运动会", "助威").ok
    assert not pond.handle("a", "a", "运动会", "助威").ok
    pond.now[0] += 86400
    assert pond.handle("a", "a", "运动会", "助威").ok
    pond._end("graduated", pond.clock())
    pond.handle("a", "a", "再养一条")
    assert not pond.handle("a", "a", "运动会", old).ok
    pond.now[0] = datetime(2026, 11, 23, 0, 0).timestamp()
    # preserve fish life while checking calendar rollover
    pond.state.fish.last_tick = pond.now[0]
    assert not pond.handle("a", "a", "运动会", old).ok
    assert not pond.state.sports_week["entries"]
    enter(pond)
    assert pond.state.users["a"].sports_badges == 2


def test_old_save_defaults_and_iso_year_boundary(pond):
    old = PondState.from_dict({"users": {"a": {}}})
    assert old.sports_week is None and old.users["a"].sports_badges == 0
    pond.now[0] = datetime(2027, 1, 1, 12).timestamp()
    pond.state.fish.last_tick = pond.now[0]
    pond.handle("a", "甲", "运动会")
    assert pond.state.sports_week["week"] == "2026-W53"
