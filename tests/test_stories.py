"""分支故事与每日共同目标：权限、真实收益、跨日、轮回和落盘。"""
import random
import time

import pytest

from game import GameConfig, JsonStore, Pond, PondState, UserInfo
from game.adventures import STORIES
from render import render_markdown


@pytest.fixture
def pond():
    cfg = GameConfig(daily_bonus_snacks=0, decay_mood=0, decay_satiety=0, decay_clean=0)
    cfg.rescue.chance = 0
    now = [time.mktime((2026, 11, 17, 12, 0, 0, 0, 0, -1))]
    p = Pond.create("story:test", cfg, lambda: now[0], random.Random(42))
    p.test_now = now
    return p


def choose(pond, pick, uid="a"):
    return pond.handle(uid, uid, "奇遇", pick)


def test_adventure_owner_cost_collection_and_replay(pond):
    result = pond.handle("a", "a", "奇遇")
    assert result.ok and len(STORIES) == 12
    pond.state.pending_adventure["story"] = "crab"
    energy = pond.state.fish.energy
    token = pond.state.pending_adventure["token"]
    assert not choose(pond, "帮忙", "b").ok
    assert not choose(pond, "帮忙@old").ok
    assert not choose(pond, "随便").ok
    assert pond.state.fish.energy == energy
    assert pond.handle("b", "b", "奇遇").ok  # 只能围观，不消耗 b 的次数
    assert pond.state.users["b"].last_adventure_day is None
    assert choose(pond, "帮忙@" + token).ok
    assert pond.state.users["a"].keepsakes == {"小蟹谢礼": 1}
    assert pond.state.fish.energy == energy - pond.cfg.adventure.help_energy
    assert "crab_helped" in pond.state.story_flags
    assert not choose(pond, "帮忙@" + token).ok
    assert not pond.handle("a", "a", "奇遇").ok
    assert pond.handle("b", "b", "奇遇").ok
    assert "小蟹谢礼" in " ".join(pond.handle("a", "a", "纪念物").lines)


@pytest.mark.parametrize("chance,won", [(1.0, True), (0.0, False)])
def test_explore_branch_and_actual_deltas(pond, chance, won):
    pond.cfg.adventure.success_chance = chance
    pond.handle("a", "a", "奇遇")
    pond.state.pending_adventure["story"] = "crab"
    assert not choose(pond, "深入").ok
    result = choose(pond, "探索")
    assert result.deltas["活力"] == -4
    assert pond.state.pending_adventure["node"] == "deep"
    assert not choose(pond, "探索").ok
    pond.state.fish.mood = 99 if won else 1
    result = choose(pond, "深入")
    assert result.ok and result.deltas["活力"] == -6
    assert result.deltas["心情"] == (1 if won else -1)
    assert bool(pond.state.users["a"].keepsakes) == won
    assert pond.state.pending_adventure is None
    assert "crab_helped" not in pond.state.story_flags


def test_escape_timeout_and_insufficient_energy(pond):
    pond.state.fish.energy = 0
    assert not pond.handle("a", "a", "奇遇").ok
    assert pond.state.users["a"].last_adventure_day is None
    pond.state.fish.energy = 5
    pond.handle("a", "a", "奇遇")
    assert not choose(pond, "帮忙").ok
    assert choose(pond, "探索").ok
    assert not choose(pond, "深入").ok
    assert choose(pond, "返回").ok
    assert pond.state.fish.energy == 1 and not pond.state.users["a"].keepsakes
    pond.state.fish.energy = 80
    pond.handle("b", "b", "奇遇")
    pond.test_now[0] += 600
    assert not choose(pond, "帮忙", "b").ok
    assert pond.state.pending_adventure is None
    assert not pond.handle("b", "b", "奇遇").ok
    pond.test_now[0] += 86400
    assert pond.handle("b", "b", "奇遇").ok
    assert choose(pond, "绕行", "b").ok
    assert not pond.state.users["b"].keepsakes


def test_heartwish_three_people_fixed_and_once(pond):
    for uid in "abc":
        pond.state.users[uid] = UserInfo(uid, True, last_care_at=pond.clock())
    pond.handle("a", "a", "心愿")
    wish = pond.state.daily_heartwish
    assert wish["goal"] == 3
    assert pond.handle("a", "a", "逗鱼").ok
    assert len(wish["got"]) == 1
    assert not pond.handle("b", "b", "逗鱼").ok  # 全塘动作冷却，拒绝不计
    pond.test_now[0] += 901
    pond.handle("a", "a", "逗鱼")
    assert len(wish["got"]) == 1
    pond.test_now[0] += 901
    pond.handle("b", "b", "逗鱼")
    assert len(wish["got"]) == 2
    pond.state.users["d"] = UserInfo("d", True, last_care_at=pond.clock())
    pond.handle("d", "d", "心愿")
    assert wish["goal"] == 3
    pond.test_now[0] += 901
    result = pond.handle("c", "c", "逗鱼")
    assert wish["done"] and len(wish["got"]) == 3
    assert any("心愿完成" in line for line in result.lines)
    pond.test_now[0] += 901
    result = pond.handle("d", "d", "逗鱼")
    assert not any("心愿完成" in line for line in result.lines)


def test_zero_gain_overfeeding_and_state_specific_goal(pond):
    pond.state.fish.satiety = 30
    pond.handle("a", "a", "心愿")
    assert pond.state.daily_heartwish["kind"] == "food"
    pond.state.fish.satiety = 100
    pond.handle("a", "a", "投喂")
    assert not pond.state.daily_heartwish["got"]
    pond.state.fish.satiety = 30
    pond.handle("a", "a", "加餐")
    assert pond.state.daily_heartwish["done"]
    pond.test_now[0] += 86400
    pond.state.fish.clean = 10
    pond.handle("a", "a", "心愿")
    assert pond.state.daily_heartwish["kind"] == "clean"
    assert pond.state.daily_heartwish["goal"] == 1
    assert not pond.state.daily_heartwish["done"]
    pond.state.fish.clean = 100
    pond.handle("a", "a", "净水")
    assert not pond.state.daily_heartwish["got"]


def test_save_reload_restart_and_old_fields(pond, tmp_path):
    pond.handle("a", "a", "逗鱼")
    assert pond.state.daily_heartwish["done"]
    pond.handle("a", "a", "奇遇")
    pond.state.pending_adventure["story"] = "crab"
    choose(pond, "探索")
    store = JsonStore(tmp_path)
    store.save(pond.state)
    loaded = store.load(pond.state.session_key)
    assert loaded.to_dict() == pond.state.to_dict()
    restored = Pond(loaded, pond.cfg, pond.clock, random.Random(4))
    assert restored.handle("a", "a", "奇遇", "返回").ok
    pond.state.users["a"].keepsakes["测试贝壳"] = 1
    pond.state.fish.exp, pond.state.fish.weight = 600, 90
    pond.handle("a", "a", "出栏")
    assert pond.state.pending_adventure is None
    assert not pond.handle("b", "b", "奇遇").ok
    assert pond.handle("a", "a", "纪念物").ok
    assert pond.handle("a", "a", "再养一条").ok
    assert pond.state.daily_heartwish["done"]
    assert pond.state.users["a"].keepsakes["测试贝壳"] == 1
    assert not pond.handle("a", "a", "奇遇").ok
    old = pond.state.to_dict()
    for key in ("pending_adventure", "daily_heartwish", "story_flags"):
        del old[key]
    for key in ("last_care_at", "last_adventure_day", "keepsakes"):
        del old["users"]["a"][key]
    migrated = PondState.from_dict(old)
    assert migrated.daily_heartwish is None and migrated.pending_adventure is None
    assert migrated.users["a"].keepsakes == {} and migrated.story_flags == []


def test_panel_shows_heartwish_in_markdown(pond):
    result = pond.handle("a", "a", "养鱼")
    assert "摸摸头" in render_markdown(result)


@pytest.mark.parametrize("story", list(STORIES))
def test_all_stories_can_finish(pond, story):
    pond.handle("a", "a", "奇遇")
    pond.state.pending_adventure["story"] = story
    result = choose(pond, "帮忙")
    assert result.ok
    assert pond.state.users["a"].keepsakes == {STORIES[story][3]: 1}


def test_story_unlock_filters_pool_and_expired_button(pond):
    class PickLast(random.Random):
        def randrange(self, stop):
            return stop - 1
    pond.rng = PickLast()
    pond.handle("a", "a", "奇遇")
    assert pond.state.pending_adventure["story"] != "crab_return"
    old_token = pond.state.pending_adventure["token"]
    choose(pond, "绕行")
    pond.state.story_flags.append("crab_helped")
    pond.test_now[0] += 86400
    pond.handle("a", "a", "奇遇")
    assert pond.state.pending_adventure["story"] == "crab_return"
    assert not choose(pond, "帮忙@" + old_token).ok
    assert not pond.state.users["a"].keepsakes
