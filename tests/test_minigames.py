"""互动玩法：独立进度、协作约束、奖励幂等和存档兼容。"""
import random
from copy import deepcopy

import pytest

from game import GameConfig, JsonStore, Pond, PondState


@pytest.fixture
def pond():
    cfg = GameConfig(daily_bonus_snacks=0)
    cfg.rescue.chance = 0
    pond = Pond.create("games:test", config=cfg, clock=lambda: 1_700_000_000,
                       rng=random.Random(42))
    pond.handle("a", "甲", "小游戏")
    return pond


def test_minigame_commands_wait_for_games_menu(pond):
    locked = Pond.create(
        "games:entry", config=GameConfig(), clock=lambda: 1_700_000_000,
        rng=random.Random(42),
    )
    before = deepcopy(locked.state.to_dict())

    result = locked.handle("a", "甲", "猜鱼")

    assert not result.ok
    assert result.title == "先打开小游戏"
    assert "请先打开" in result.lines[0]
    assert locked.state.to_dict() == before

    menu = locked.handle("a", "甲", "小游戏")
    assert menu.title == "鱼塘小游戏"
    assert locked.state.minigames_unlocked is True
    assert locked.handle("a", "甲", "猜鱼").ok


@pytest.mark.parametrize(
    "command,arg",
    [
        ("玩耍", None),
        ("Token陪玩", None),
        ("猜鱼", None),
        ("寻宝", None),
        ("游历", None),
        ("奇遇", None),
        ("运动会", None),
    ],
)
def test_locked_minigame_commands_do_not_change_pond(command, arg):
    cfg = GameConfig(daily_bonus_snacks=0)
    cfg.rescue.chance = 0
    locked = Pond.create(
        "games:locked", config=cfg, clock=lambda: 1_700_000_000, rng=random.Random(42)
    )
    before = deepcopy(locked.state.to_dict())

    result = locked.handle("a", "甲", command, arg)

    assert not result.ok
    assert result.title == "先打开小游戏"
    assert locked.state.to_dict() == before


def test_guess_independent_resume_and_single_reward(pond):
    assert pond.handle("a", "甲", "猜鱼").ok
    game = pond.state.pending_guess["a"]
    energy = pond.state.fish.energy
    assert pond.handle("a", "甲", "猜鱼").ok
    assert pond.state.fish.energy == energy
    assert pond.state.pending_guess["a"] == game
    assert pond.handle("b", "乙", "猜鱼").ok
    assert pond.state.pending_guess["a"] is not pond.state.pending_guess["b"]
    wallet = pond.state.users["a"].rice
    answer = str(game["hidden"])
    result = pond.handle("a", "甲", "猜鱼", answer)
    assert result.ok and result.deltas["白饭"] == 1
    assert pond.state.users["a"].rice == wallet + 1
    assert "b" in pond.state.pending_guess and "a" not in pond.state.pending_guess
    assert not pond.handle("a", "甲", "猜鱼", answer).ok
    assert not pond.handle("a", "甲", "猜鱼").ok
    assert pond.state.users["a"].rice == wallet + 1
    assert pond.state.users["a"].snacks == pond.cfg.start_snacks


def test_guess_hints_invalid_input_and_loss(pond):
    pond.handle("a", "甲", "猜鱼")
    game = pond.state.pending_guess["a"]
    game["hidden"] = 7
    for value in ("0", "11", "1.5", "abc", "9" * 5000):
        assert not pond.handle("a", "甲", "猜鱼", value).ok
        assert game["left"] == 3
    result = pond.handle("a", "甲", "猜鱼", "3")
    assert "小了" in result.lines[0] and game["low"] == 4
    assert not pond.handle("a", "甲", "猜鱼", "3").ok
    assert game["left"] == 2
    result = pond.handle("a", "甲", "猜鱼", "9")
    assert "大了" in result.lines[0] and game["high"] == 8
    result = pond.handle("a", "甲", "猜鱼", "6")
    assert result.ok and result.deltas["心情"] == 2
    assert pond.state.users["a"].snacks == pond.cfg.start_snacks


def test_guess_expiry_energy_and_nickname_gates(pond):
    assert not pond.handle("a", "", "猜鱼").ok
    assert not pond.state.pending_guess
    pond.state.fish.energy = 0
    assert not pond.handle("b", "乙", "猜鱼").ok
    assert pond.state.users["b"].last_guess_at is None
    pond.state.fish.energy = 80
    pond.handle("b", "乙", "猜鱼")
    start = pond.clock()
    answer = str(pond.state.pending_guess["b"]["hidden"])
    pond.clock = lambda: start + 300
    assert not pond.handle("b", "乙", "猜鱼", answer).ok
    assert not pond.handle("b", "乙", "猜鱼").ok
    assert not pond.state.pending_guess
    pond.clock = lambda: start + 1800
    assert pond.handle("b", "乙", "猜鱼").ok
    assert "b" in pond.state.pending_guess



def test_treasure_order_distinct_users_and_rewards(pond):
    assert pond.handle("a", "甲", "寻宝").ok
    assert not pond.handle("a", "甲", "寻宝", "开箱").ok
    assert not pond.state.pending_treasure["got"]
    assert pond.handle("a", "甲", "寻宝", "探路").ok
    assert not pond.handle("a", "甲", "寻宝", "搬石").ok
    assert pond.handle("b", "乙", "寻宝", "搬石").ok
    assert pond.handle("c", "丙", "寻宝", "开箱").ok
    assert pond.state.pending_treasure is None
    assert pond.state.community_exp == 3
    for uid in "abc":
        assert pond.state.users[uid].rice == pond.cfg.start_rice + 2
        assert pond.state.users[uid].snacks == pond.cfg.start_snacks
        assert pond.state.users[uid].intimacy == 1
        assert pond.state.users[uid].total_interactions == 1
    assert not pond.handle("c", "丙", "寻宝", "开箱").ok
    assert not pond.handle("a", "甲", "寻宝").ok


def test_treasure_timeout_and_reopen(pond):
    pond.handle("a", "甲", "寻宝")
    pond.handle("a", "甲", "寻宝", "探路")
    start = pond.clock()
    pond.clock = lambda: start + 600
    assert not pond.handle("b", "乙", "寻宝", "搬石").ok
    assert pond.state.pending_treasure is None
    assert pond.state.users["a"].snacks == pond.cfg.start_snacks
    assert pond.state.users["a"].intimacy == 0
    assert not pond.handle("a", "甲", "寻宝").ok
    pond.clock = lambda: start + 3600
    assert pond.handle("a", "甲", "寻宝").ok


def test_save_reload_old_save_and_end(pond, tmp_path):
    pond.handle("a", "甲", "猜鱼")
    pond.handle("a", "甲", "寻宝")
    pond.handle("a", "甲", "寻宝", "探路")
    store = JsonStore(tmp_path)
    store.save(pond.state)
    loaded = store.load(pond.state.session_key)
    assert loaded.to_dict() == pond.state.to_dict()
    restored = Pond(loaded, pond.cfg, pond.clock, random.Random(9))
    assert restored.handle("a", "甲", "猜鱼", str(loaded.pending_guess["a"]["hidden"])).ok
    assert restored.handle("b", "乙", "寻宝", "搬石").ok
    assert restored.handle("c", "丙", "寻宝", "开箱").ok
    old = pond.state.to_dict()
    del old["pending_guess"], old["pending_treasure"]
    del old["users"]["a"]["last_guess_at"]
    assert PondState.from_dict(old).pending_guess == {}
    assert PondState.from_dict(old).users["a"].last_guess_at is None
    pond.state.fish.exp, pond.state.fish.weight = 600, 90
    assert pond.handle("a", "甲", "出栏").ok
    assert not pond.state.pending_guess and pond.state.pending_treasure is None
    for command in ("小游戏", "猜鱼", "寻宝"):
        assert not pond.handle("a", "甲", command).ok
    assert pond.handle("a", "甲", "再养一条").ok
    assert not pond.handle("a", "甲", "猜鱼").ok  # 换鱼不会跳过个人冷却
