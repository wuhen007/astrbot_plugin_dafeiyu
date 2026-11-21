"""周委托：有效贡献、日上限、固定目标、奖励与跨周存档。"""
import random
from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from game import GameConfig, JsonStore, Pond, PondState, UserInfo
from game.store import load_or_create


@pytest.fixture
def pond():
    cfg = GameConfig(daily_bonus_snacks=0, decay_mood=0, decay_satiety=0, decay_clean=0)
    cfg.rescue.chance = 0
    now = [datetime(2026, 11, 18, 12).timestamp()]
    p = Pond.create("weekly:test", cfg, lambda: now[0], random.Random(42))
    p.now = now
    return p


def care(p, command, uid="a"):
    p.state.users.setdefault(uid, UserInfo(display_name=uid, nick_set=True)).rice = 100
    p.state.fish.cooldowns.clear()
    for user in p.state.users.values():
        user.cooldowns.clear()
    p.state.fish.satiety, p.state.fish.mood, p.state.fish.clean = 40, 30, 20
    day = datetime.fromtimestamp(p.clock()).date().isoformat()
    p.state.daily_heartwish = {"day": day, "done": True}
    return p.handle(uid, uid, command)


def complete(p):
    for _ in range(2):
        for cmd in ("投喂", "净水", "逗鱼"):
            care(p, cmd)
    p.now[0] += 86400
    care(p, "投喂")
    return care(p, "逗鱼")


def test_single_group_completion_rewards_once(pond):
    pond.handle("a", "甲", "委托")
    assert pond.state.weekly_quest["goals"] == {"food": 3, "clean": 2, "pet": 3}
    result = complete(pond)
    assert pond.state.weekly_quest["done"]
    assert "已自动到账" in " ".join(result.lines)
    assert pond.state.users["a"].materials == 2
    assert pond.state.users["a"].keepsakes["周委托纪念章"] == 1
    before = deepcopy(pond.state.weekly_reports)
    for cmd in ("委托", "周报", "纪念物"):
        pond.handle("a", "甲", cmd)
    care(pond, "投喂")
    assert pond.state.users["a"].materials == 2
    assert pond.state.weekly_reports == before
    assert sum(p["count"] for p in before[0]["people"]) == 8


def test_daily_limit_feed_snack_share_and_invalid_actions(pond):
    care(pond, "投喂")
    # The ordinary action cooldown is still enforced.
    pond.handle("a", "a", "投喂")
    assert pond.state.weekly_quest["progress"]["food"] == 1
    pond.state.users["a"].snacks = 5
    care(pond, "加餐")
    care(pond, "投喂")
    assert pond.state.weekly_quest["progress"]["food"] == 2
    pond.state.fish.cooldowns.clear()
    for user in pond.state.users.values():
        user.cooldowns.clear()
    pond.state.fish.mood = 100
    pond.handle("a", "a", "逗鱼")
    assert pond.state.weekly_quest["progress"]["pet"] == 0
    pond.state.fish.cooldowns.clear()
    for user in pond.state.users.values():
        user.cooldowns.clear()
    pond.state.fish.satiety = 100
    pond.handle("a", "a", "投喂")
    assert pond.state.weekly_quest["progress"]["food"] == 2
    pond.now[0] += 86400
    care(pond, "投喂")
    assert pond.state.weekly_quest["progress"]["food"] == 3


def test_heartwish_bonus_cannot_count_invalid_pet(pond):
    # A completed heartwish adds mood after the action; weekly progress uses the action's actual gain.
    pond.handle("a", "a", "委托")
    pond.state.fish.mood = 100
    pond.handle("a", "a", "逗鱼")
    assert pond.state.weekly_quest["progress"]["pet"] == 0


def test_fixed_active_tier_late_join_and_distinct_participants(pond):
    for uid in "abc":
        pond.state.users[uid] = UserInfo(uid, True, last_care_at=pond.clock())
    pond.state.users["old"] = UserInfo("旧鱼友", True, last_care_at=pond.clock() - 8 * 86400)
    pond.handle("a", "a", "委托")
    quest = pond.state.weekly_quest
    assert quest["people"] == 3 and quest["goals"]["clean"] == 6
    pond.handle("d", "d", "委托")
    assert quest["people"] == 3
    # One player can fill counts over several days, but cannot satisfy the people requirement.
    for day in range(5):
        for _ in range(2):
            for cmd in ("投喂", "净水", "逗鱼"):
                care(pond, cmd)
        if day < 4:
            pond.now[0] += 86400
    assert quest["progress"] == quest["goals"] and not quest["done"]
    care(pond, "逗鱼", "b")
    assert not quest["done"]
    care(pond, "净水", "c")
    assert quest["done"]
    assert {k: pond.state.users[k].materials for k in "abcd"} == {"a": 2, "b": 2, "c": 2, "d": 0}
    assert quest["progress"] == quest["goals"]


def test_restart_and_json_reload_keep_progress_and_immutable_report(pond, tmp_path):
    care(pond, "投喂")
    before = deepcopy(pond.state.weekly_quest)
    pond._end("graduated", pond.clock())
    pond.handle("a", "a", "再养一条")
    assert pond.state.weekly_quest == before
    store = JsonStore(tmp_path)
    store.save(pond.state)
    restored = load_or_create(store, pond.state.session_key, pond.cfg, pond.clock, random.Random(1))
    assert restored.state.weekly_quest == before
    restored.now = pond.now
    complete(restored)
    report = deepcopy(restored.state.weekly_reports[0])
    restored.handle("a", "a", "昵称", "新名号")
    assert restored.state.weekly_reports[0] == report
    store.save(restored.state)
    assert store.load(pond.state.session_key).weekly_reports == [report]


def test_week_rollover_resets_without_retroactive_rewards(pond):
    care(pond, "净水")
    pond.now[0] = datetime(2026, 11, 23).timestamp()
    pond.state.fish.last_tick = pond.clock()
    pond.handle("a", "a", "委托")
    assert pond.state.weekly_quest["week"] == "2026-W48"
    assert not any(pond.state.weekly_quest["progress"].values())
    assert not pond.state.weekly_reports and pond.state.users["a"].materials == 0
    pond.now[0] = datetime(2027, 1, 1, 12).timestamp()
    pond.state.fish.last_tick = pond.clock()
    pond.handle("a", "a", "委托")
    assert pond.state.weekly_quest["week"] == "2026-W53"


def test_old_saves_and_report_retention(pond):
    old = PondState.from_dict({})
    assert old.weekly_quest is None and old.weekly_reports == []
    assert "还没有" in " ".join(pond.handle("a", "a", "周报").lines)
    base = datetime(2026, 11, 18, 12)
    for i in range(10):
        pond.now[0] = (base + timedelta(weeks=i)).timestamp()
        pond.state.fish.last_tick = pond.clock()
        complete(pond)
    assert len(pond.state.weekly_reports) == 8
    assert pond.state.users["a"].materials == 20
    assert pond.state.users["a"].keepsakes["周委托纪念章"] == 10
    assert PondState.from_dict(pond.state.to_dict()).to_dict() == pond.state.to_dict()
