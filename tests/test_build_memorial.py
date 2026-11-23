"""材料记账、公共装修、历史快照和存档兼容性。"""
import random
from copy import deepcopy

import pytest

from game import GameConfig, Pond, PondState, UserInfo
from game.decorations import completed
from game.engine import memorial_lines


@pytest.fixture
def pond():
    cfg = GameConfig(daily_bonus_snacks=0, decay_mood=0, decay_satiety=0, decay_clean=0)
    cfg.rescue.chance = 0
    pond = Pond.create("build:test", cfg, lambda: 1795000000.0, random.Random(42))
    pond.handle("a", "甲", "小游戏")
    return pond


def graduate(pond):
    f = pond.state.fish
    f.exp, f.weight, f.mood = 600, 90, 80
    return pond.handle("a", "甲", "出栏")


def test_material_sources_once_and_no_reward_for_escape(pond):
    pond.handle("a", "甲", "逗鱼")
    assert pond.state.users["a"].materials == 1
    pond.handle("a", "甲", "逗鱼")
    pond.handle("a", "甲", "心愿")
    assert pond.state.users["a"].materials == 1
    pond.handle("a", "甲", "奇遇")
    pond.handle("a", "甲", "奇遇", "帮忙")
    assert pond.state.users["a"].materials == 2
    pond.handle("a", "甲", "奇遇", "帮忙")
    assert pond.state.users["a"].materials == 2
    pond.handle("b", "乙", "奇遇")
    pond.handle("b", "乙", "奇遇", "绕行")
    assert pond.state.users["b"].materials == 0


def test_heartwish_rewards_every_participant_once(pond):
    for uid in "abc":
        pond.state.users[uid] = UserInfo(uid, True, last_care_at=pond.clock())
    pond.handle("a", "a", "心愿")
    for uid in "abc":
        pond.state.fish.cooldowns.clear()
        for user in pond.state.users.values():
            user.cooldowns.clear()
        pond.state.fish.mood = 50
        pond.handle(uid, uid, "逗鱼")
    assert [pond.state.users[k].materials for k in "abc"] == [1, 1, 1]
    assert any("心愿" in event for event in pond.state.round_events)
    pond.state.fish.cooldowns.clear()
    for user in pond.state.users.values():
        user.cooldowns.clear()
    pond.handle("a", "a", "逗鱼")
    assert pond.state.users["a"].materials == 1


def test_build_accounting_all_donors_and_completed_charge_guard(pond):
    pond.handle("a", "甲", "建设")
    pond.handle("b", "乙", "建设")
    assert not pond.handle("a", "甲", "建设", "水草角").ok
    pond.state.users["a"].materials = 3
    pond.state.users["b"].materials = 3
    before = deepcopy(pond.state.fish.to_dict())
    for uid in "aabbb":
        assert pond.handle(uid, uid, "建设", "水草角").ok
    assert completed(pond.state.decorations) == ["水草角"]
    assert pond.state.decorations["水草角"] == {"a": 2, "b": 3}
    assert pond.state.users["a"].materials == 1
    assert not pond.handle("a", "a", "建设", "水草角").ok
    assert not pond.handle("a", "a", "建设", "不存在").ok
    assert pond.state.users["a"].materials == 1
    assert pond.state.fish.to_dict() == before  # 外观不改变数值


def test_snapshot_roles_stable_after_restart_and_rename(pond):
    pond.handle("a", "甲", "逗鱼")
    pond.handle("b", "乙", "净水")
    pond.state.decorations["水草角"] = {"a": 5}
    result = graduate(pond)
    assert result.event == "graduated" and result.data["memorial"]
    page = deepcopy(pond.state.archive[0])
    wallets = [u.snacks for u in pond.state.users.values()]
    assert "净水担当：乙" in " ".join(pond.handle("a", "甲", "纪念册").lines)
    graduate(pond)
    pond._end("graduated", pond.clock())
    assert len(pond.state.archive) == 1
    assert [u.snacks for u in pond.state.users.values()] == wallets
    pond.handle("a", "甲", "再养一条")
    assert not pond.state.round_stats and not pond.state.round_events
    assert completed(pond.state.decorations) == ["水草角"]
    assert pond.state.users["a"].materials == 1
    pond.handle("a", "甲", "昵称", "改了名字")
    pond.state.decorations["月亮灯"] = {"a": 12}
    pond.handle("a", "改了名字", "逗鱼")
    assert pond.state.archive[0] == page
    assert pond.handle("a", "甲", "合影", "1").data["memorial"] == page
    assert not pond.handle("a", "甲", "纪念册", "0").ok
    assert not pond.handle("a", "甲", "纪念册", "abc").ok
    graduate(pond)
    older = pond.handle("a", "甲", "纪念册", "1")
    assert any(b.command == "养鱼 纪念册 2" for b in older.buttons)


def test_old_archive_and_new_state_round_trip(pond):
    old = PondState.from_dict({"archive": [{"fish_name": "旧鱼", "reason": "graduated"}]})
    assert old.decorations == {} and old.round_stats == {} and old.round_events == []
    pond.state.archive = old.archive
    r = pond.handle("a", "甲", "纪念册")
    assert "旧版档案" in " ".join(r.lines)
    pond.handle("a", "甲", "逗鱼")
    pond.state.decorations["贝壳小屋"] = {"a": 2}
    restored = PondState.from_dict(pond.state.to_dict())
    assert restored.to_dict() == pond.state.to_dict()
    assert restored.users["a"].materials == 1


def test_partial_memorial_snapshot_uses_safe_defaults(tmp_path):
    from render.fishimg import render_memorial_png

    page = {"fish_name": "旧鱼", "reason": "graduated",
            "memorial": {"fish": {}, "events": []}}
    lines = memorial_lines(page)
    assert "本轮参与照料：0位鱼友" in lines
    assert "合影装饰：自然鱼塘" in lines
    out = tmp_path / "partial-memorial.png"
    render_memorial_png(page, str(out))
    assert out.exists() and out.stat().st_size > 1000

    malformed = {"fish_name": "旧鱼", "memorial": []}
    assert "旧版档案" in " ".join(memorial_lines(malformed))
    render_memorial_png(malformed, str(tmp_path / "malformed-memorial.png"))


def test_runaway_and_event_log_bound(pond):
    for n in range(15):
        pond._round_event(str(n))
    assert pond.state.round_events == list(map(str, range(9, 15)))
    pond._end("runaway", pond.clock(), force_grade="C")
    page = pond.handle("a", "甲", "纪念册").data["memorial"]
    assert page["reason"] == "runaway"
    assert page["memorial"]["events"] == list(map(str, range(9, 15)))
