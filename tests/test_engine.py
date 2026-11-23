"""引擎核心循环 / 成长 / 结算 / 重开 / 衰减 的单元测试。

时钟与随机数全部注入，测试完全确定。
"""

import random

import pytest

from game import GameConfig, Pond, PondState, growth
from game.engine import ActionResult
from game.state import UserInfo

H = 3600.0


class FakeClock:
    def __init__(self, now=1_700_000_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds

    def advance_hours(self, hours):
        self.advance(hours * H)


def make_pond(**cfgkw):
    cfg = GameConfig(**cfgkw)
    clock = FakeClock()
    rng = random.Random(42)
    pond = Pond.create("test:group:1", config=cfg, clock=clock, rng=rng)
    pond.handle("u1", "小明", "小游戏")
    return pond, clock


# ---------- 基础动作 ----------
def test_feed_basic():
    pond, clock = make_pond()
    r = pond.handle("u1", "小明", "投喂")
    assert r.ok
    labels = [button.label for button in r.buttons]
    assert "投喂" not in labels
    assert any(label.endswith("看看鱼塘") for label in labels)
    assert any(label.endswith("小游戏") for label in labels)
    assert r.deltas["饱腹"] == pytest.approx(22.0)
    assert 1.5 <= r.deltas["体重"] <= 3.5
    assert r.deltas["经验"] == 8
    assert pond.state.users["u1"].cooldowns["feed"] == clock.now
    r2 = pond.handle("u1", "小明", "投喂")  # 冷却内被拒
    assert not r2.ok
    clock.advance_hours(0.75)
    r3 = pond.handle("u1", "小明", "投喂")
    assert r3.ok


def test_sign_result_hides_completed_action_but_keeps_navigation():
    pond, _ = make_pond()
    result = pond.handle("u1", "小明", "签到")
    labels = [button.label for button in result.buttons]
    assert "签到" not in labels
    assert any(label.endswith("看看鱼塘") for label in labels)
    assert any(label.endswith("小游戏") for label in labels)


def test_daily_diary_unlocks_after_successful_interaction_and_uses_real_data():
    pond, _ = make_pond()
    before = pond.handle("u1", "小明", "养鱼")
    assert not any(button.command == "养鱼 日记" for button in before.buttons)

    feed = pond.handle("u1", "小明", "投喂")
    assert any(button.command == "养鱼 日记" for button in feed.buttons)
    assert pond.state.daily_diary["counts"] == {"投喂": 1}

    diary = pond.handle("u1", "小明", "日记")
    text = "\n".join(diary.lines)
    assert diary.title == "大肥鱼的今日手记"
    assert "投喂 1 次" in text
    assert f"饱腹 {round(pond.state.fish.satiety)}" in text
    assert 160 <= len(text) <= 260
    assert any(button.command == "养鱼" for button in diary.buttons)


def test_failed_interaction_does_not_add_to_daily_diary():
    pond, _ = make_pond()
    pond.handle("u1", "小明", "投喂")
    result = pond.handle("u1", "小明", "投喂")
    assert not result.ok
    assert pond.state.daily_diary["counts"] == {"投喂": 1}


def test_daily_diary_unlocks_after_a_multistep_game_finishes():
    pond, _ = make_pond()
    menu = pond.handle("u1", "小明", "玩耍")
    assert not any(button.command == "养鱼 日记" for button in menu.buttons)

    result = pond.handle("u1", "小明", "玩耍", "①")
    assert any(button.command == "养鱼 日记" for button in result.buttons)


def test_daily_diary_stays_hidden_on_a_sports_strategy_prompt():
    pond, _ = make_pond()
    menu = pond.handle("u1", "小明", "运动会", "冲刺")
    assert menu.title.endswith("选择训练策略")
    assert not any(button.command == "养鱼 日记" for button in menu.buttons)
    assert pond.state.daily_diary is None


def test_daily_diary_stays_hidden_on_read_only_game_menus():
    for command in ("运动会", "建设"):
        pond, _ = make_pond()
        result = pond.handle("u1", "小明", command)
        assert not any(button.command == "养鱼 日记" for button in result.buttons)
        assert pond.state.daily_diary is None


def test_daily_diary_rolls_over_at_local_midnight():
    pond, clock = make_pond()
    pond.handle("u1", "小明", "逗鱼")
    old_day = pond.state.daily_diary["day"]
    clock.advance(24 * H)

    result = pond.handle("u1", "小明", "日记")

    assert "今天还没有互动" in "\n".join(result.lines)
    assert pond.state.daily_diary["day"] != old_day
    assert pond.state.daily_diary["counts"] == {}
    assert not any(button.command == "养鱼 日记" for button in result.buttons)


def test_daily_diary_keeps_only_a_bounded_set_of_notable_events():
    pond, _ = make_pond()
    result = ActionResult(
        lines=[f"遇见老朋友：{index}-{'鱼' * 120}" for index in range(8)]
    )

    pond._record_daily_diary("travel", result)

    highlights = pond.state.daily_diary["highlights"]
    assert len(highlights) == 5
    assert all(len(highlight) <= 100 for highlight in highlights)
    diary = pond.handle("u1", "小明", "日记")
    text = "\n".join(diary.lines)
    assert "遇见老朋友：0" in text
    assert "梁帝" not in text
    assert 160 <= len(text) <= 260


def test_daily_diary_stays_compact_with_many_interaction_types():
    pond, _ = make_pond()
    summary = pond._ensure_daily_diary()
    summary["counts"] = {
        label: 1
        for label in (
            "投喂", "加餐", "逗鱼", "净水", "玩耍", "Token陪玩", "称重", "签到", "钓鱼",
            "救援", "许愿", "运动会", "游历", "奇遇", "猜鱼", "寻宝", "建设",
        )
    }
    summary["stats"] = {
        "name": "团团", "satiety": 70, "mood": 70, "clean": 80, "energy": 80, "weight": 8.0
    }

    diary = pond.handle("u1", "小明", "日记")

    assert 160 <= len("\n".join(diary.lines)) <= 260


def test_travel_buttons_do_not_expose_internal_token():
    pond, _ = make_pond()
    card = pond._travel_card(
        {"step": 0, "steps": 3, "token": "1795253606138"}
    )
    assert [button.command for button in card.buttons] == [
        "养鱼 游历 1",
        "养鱼 游历 2",
    ]


def test_pet_clean_snack():
    pond, clock = make_pond()
    r = pond.handle("u1", "小明", "逗鱼")
    assert r.deltas["心情"] == pytest.approx(6.0 + pond.cfg.heartwish.mood_gain)
    assert r.deltas["亲密"] == 1
    pond.state.fish.clean = 20.0  # 压低起点，避免 80+35 触顶截断
    clock.advance_hours(0.3)
    r = pond.handle("u1", "小明", "净水")
    assert r.deltas["洁净"] == pytest.approx(35.0)
    clock.advance_hours(0.3)
    r = pond.handle("u1", "小明", "加餐")  # 白饭 -1，今日补贴已领
    assert r.deltas["白饭"] == pytest.approx(-1.0)
    assert pond.state.users["u1"].rice == 5  # 5(初始) +1(补贴) -1(加餐)
    assert pond.state.users["u1"].snacks == 0  # 绝版海苔不参与新玩法


def test_snack_refuse_when_empty():
    pond, _ = make_pond()
    pond.state.users["u9"] = UserInfo(display_name="阿九", rice=0)
    r = pond.handle("u9", "阿九", "加餐")
    assert not r.ok


def test_overfeed():
    pond, _ = make_pond()
    fish = pond.state.fish
    fish.satiety = 95.0
    r = pond.handle("u1", "小明", "投喂")
    assert r.ok
    assert r.deltas["体重"] == pytest.approx(-1.0)
    assert r.deltas["心情"] == pytest.approx(-6.0)
    assert fish.satiety == pytest.approx(95.0)  # 撑到不涨饱腹
    assert r.deltas["经验"] == 2


def test_cooldown_pet():
    pond, clock = make_pond()
    assert pond.handle("u1", "小明", "逗鱼").ok
    clock.advance(5 * 60)
    assert not pond.handle("u1", "小明", "逗鱼").ok
    clock.advance(10 * 60 + 1)
    assert pond.handle("u1", "小明", "逗鱼").ok


def test_diminishing_returns():
    pond, clock = make_pond()
    r1 = pond.handle("u1", "小明", "逗鱼")
    assert r1.deltas["心情"] == pytest.approx(6.0 + pond.cfg.heartwish.mood_gain)
    clock.advance(15 * 60 + 1)
    r2 = pond.handle("u1", "小明", "逗鱼")
    assert r2.deltas["心情"] == pytest.approx(4.5)  # ×0.75
    clock.advance(15 * 60 + 1)
    r3 = pond.handle("u1", "小明", "逗鱼")
    # ×0.75² = 3.375，展示层四舍五入到 1 位小数
    assert r3.deltas["心情"] == pytest.approx(3.375, abs=0.05)


# ---------- 套圈小游戏 ----------
def test_play_success_and_miss():
    pond, clock = make_pond()
    r = pond.handle("u1", "小明", "玩耍")
    assert r.event == "game_open"
    assert pond.state.fish.energy == pytest.approx(60.0)  # 开局扣 20 活力
    pending = pond.state.pending_play
    assert pending is not None
    hidden = pending["hidden"]
    hit = pond.handle("u1", "小明", "玩耍", arg=str(hidden + 1))
    assert hit.deltas["体重"] == pytest.approx(3.0)
    assert hit.deltas["心情"] == pytest.approx(8.0)
    assert hit.deltas["经验"] == 12
    assert pond.state.pending_play is None
    # 冷却过后再开一局、猜错
    clock.advance(60 * 60 + 1)
    pond.handle("u1", "小明", "玩耍")
    hidden2 = pond.state.pending_play["hidden"]
    wrong = (hidden2 + 1) % 3 + 1
    miss = pond.handle("u1", "小明", "玩耍", arg=str(wrong))
    assert miss.deltas["心情"] == pytest.approx(3.0)
    assert "体重" not in miss.deltas


def test_play_dirty_water():
    pond, _ = make_pond()
    pond.state.fish.clean = 10.0
    pond.handle("u1", "小明", "玩耍")
    hidden = pond.state.pending_play["hidden"]
    r = pond.handle("u1", "小明", "玩耍", arg=str(hidden + 1))
    assert "体重" not in r.deltas  # 猜中也捞不到
    assert r.deltas["心情"] == pytest.approx(3.0)


def test_play_needs_energy():
    pond, _ = make_pond()
    pond.state.fish.energy = 10.0
    r = pond.handle("u1", "小明", "玩耍")
    assert not r.ok


def test_play_timeout():
    pond, clock = make_pond()
    pond.handle("u1", "小明", "玩耍")
    clock.advance(6 * 60 + 1)
    pond.handle("u1", "小明", "养鱼")  # touch 顺带清理超时
    assert pond.state.pending_play is None
    r = pond.handle("u1", "小明", "玩耍", arg="1")
    assert not r.ok


def test_play_pick_is_limited_to_the_opener():
    pond, _ = make_pond()
    pond.handle("u1", "小明", "玩耍")
    pending = pond.state.pending_play
    energy = pond.state.fish.energy

    second_open = pond.handle("u2", "小红", "玩耍")
    assert second_open.title == "套圈进行中"
    assert pond.state.pending_play == pending
    assert pond.state.fish.energy == energy

    stolen = pond.handle("u2", "小红", "玩耍", str(pending["hidden"] + 1))
    assert not stolen.ok
    assert pond.state.pending_play == pending
    assert pond.state.fish.energy == energy
    assert pond.state.users["u2"].intimacy == 0

    result = pond.handle("u1", "小明", "玩耍", str(pending["hidden"] + 1))
    assert result.ok and pond.state.pending_play is None


def test_action_cooldowns_are_per_user_and_legacy_group_cooldown_expires():
    pond, clock = make_pond()
    assert pond.handle("u1", "小明", "投喂").ok
    pond.state.fish.satiety = 40
    assert pond.handle("u2", "小红", "投喂").ok
    assert not pond.handle("u1", "小明", "投喂").ok

    migrated, migrated_clock = make_pond()
    migrated.state.fish.cooldowns["feed"] = migrated_clock.now
    assert not migrated.handle("u2", "小红", "投喂").ok
    migrated_clock.advance(45 * 60 + 1)
    assert migrated.handle("u2", "小红", "投喂").ok


def test_non_care_rewards_do_not_increase_intimacy():
    pond, _ = make_pond()
    user = pond.state.users.setdefault("u1", UserInfo(display_name="小明", nick_set=True))
    for command in ("称重", "钓鱼"):
        result = pond.handle("u1", "小明", command)
        assert "亲密" not in result.deltas
        assert user.intimacy == 0
        assert pond.state.round_stats["u1"].get("care", 0) == 0
    user.snacks = 10
    result = pond.handle("u1", "小明", "许愿")
    assert "亲密" not in result.deltas
    assert user.intimacy == 0
    assert pond.state.round_stats["u1"].get("care", 0) == 0


def test_rescue_requires_a_registered_name_before_joining():
    pond, _ = make_pond()
    pond.state.pending_rescue = {
        "kind": "test",
        "text": "求援",
        "need": 1,
        "got": {},
        "expires": pond.clock() + 600,
    }
    result = pond.handle("u9", "", "救援")
    assert result.event == "need_nick"
    assert not pond.state.pending_rescue["got"]
    pond.handle("u9", "", "昵称", "阿九")
    result = pond.handle("u9", "", "救援")
    assert result.event == "rescue"
    assert pond.state.pending_rescue is None
    assert pond.state.users["u9"].nick_set


# ---------- 成长与衰减 ----------
def test_levelup():
    pond, _ = make_pond()
    pond.state.fish.exp = 45
    r = pond.handle("u1", "小明", "投喂")
    assert pond.state.fish.exp == 53
    assert r.event == "levelup"
    assert any("进阶" in ln for ln in r.lines)


def test_decay_offline():
    pond, clock = make_pond()
    pond.handle("u1", "小明", "养鱼")  # 先 touch 一次对齐时钟
    clock.advance_hours(2)
    r = pond.handle("u1", "小明", "养鱼")
    fish = pond.state.fish
    assert fish.satiety == pytest.approx(70 - pond.cfg.decay_satiety * 2)
    assert fish.mood == pytest.approx(70 - pond.cfg.decay_mood * 2)
    assert fish.clean == pytest.approx(80 - pond.cfg.decay_clean * 2)
    assert fish.energy == pytest.approx(min(100.0, 80 + 6.0 * 2))
    assert r.data["offline"]["饱腹"] == pytest.approx(-pond.cfg.decay_satiety * 2)


def test_decay_stage_multiplier():
    pond, clock = make_pond()
    pond.state.fish.exp = 500  # Lv.6 → ×1.85
    clock.advance_hours(2)
    pond.handle("u1", "小明", "养鱼")
    assert pond.state.fish.satiety == pytest.approx(
        70 - pond.cfg.decay_satiety * 2 * pond.cfg.stage_decay_multiplier[-1]
    )


def test_pond_level_relief():
    pond, _ = make_pond()
    pond.state.community_exp = 199
    pond.handle("u1", "小明", "逗鱼")  # 第 200 次群互动
    assert pond.pond_level() == 1
    assert growth.decay_multiplier(1, 1, pond.cfg) == pytest.approx(0.96)


def test_dirty_growth_penalty():
    pond, _ = make_pond()
    pond.state.fish.clean = 10.0
    r = pond.handle("u1", "小明", "投喂")
    assert r.deltas["体重"] <= 3.5 * 0.5


# ---------- 出栏与重开 ----------
def _ready_fish(pond):
    fish = pond.state.fish
    fish.exp = 600
    fish.weight = 90.0
    fish.mood = 80.0
    fish.clean = 70.0


def test_graduate_gate():
    pond, _ = make_pond()
    pond.state.fish.exp = 600
    pond.state.fish.weight = 50.0
    r = pond.handle("u1", "小明", "出栏")
    assert not r.ok
    assert any("体重" in ln for ln in r.lines)


def test_graduate_and_rewards():
    pond, _ = make_pond()
    _ready_fish(pond)
    pond.state.users["u1"] = UserInfo(
        display_name="小明", intimacy=55, nick_set=True, snacks=5, rice=2
    )
    pond.state.users["u2"] = UserInfo(display_name="小红", intimacy=30, nick_set=True)
    pond.state.users["u3"] = UserInfo(display_name="阿三", intimacy=20, nick_set=True)
    r = pond.handle("u1", "小明", "出栏")
    assert r.event == "graduated"
    assert pond.state.status == "ended"
    s = pond.state.end_summary
    # 评分 = 90×0.4 + 80×0.25 + 70×0.15 + 55×0.2 = 77.5 → A
    assert s["score"] == pytest.approx(77.5)
    assert s["grade"] == "A"
    assert pond.state.archive[-1]["grade"] == "A"
    assert pond.state.users["u1"].rice == 14  # 第 1 名全额
    assert pond.state.users["u2"].rice == 6  # 第 2/3 名半额
    assert pond.state.users["u3"].rice == 6
    assert pond.state.users["u1"].snacks == 5  # 绝版海苔保持不变
    assert any(b.label == "再养一条" for b in r.buttons)


def test_blocked_after_end():
    pond, _ = make_pond()
    _ready_fish(pond)
    pond.handle("u1", "小明", "出栏")
    r = pond.handle("u1", "小明", "投喂")
    assert not r.ok
    assert r.event == "blocked"


def test_restart_keeps_archive_and_snacks():
    pond, _ = make_pond()
    _ready_fish(pond)
    pond.state.users["u1"] = UserInfo(
        display_name="小明", intimacy=55, nick_set=True, snacks=5, rice=2
    )
    pond.handle("u1", "小明", "出栏")
    pond.state.minigames_unlocked = True
    pond.state.daily_diary = {
        "day": "2026-11-22",
        "counts": {"投喂": 1},
        "highlights": [],
        "stats": {},
    }
    r = pond.handle("u1", "小明", "再养一条")
    assert r.ok
    st = pond.state
    assert st.status == "alive"
    assert st.fish.weight == pond.cfg.start_weight
    assert st.fish.exp == 0
    assert st.users["u1"].intimacy == 0  # 亲密跟着鱼走
    assert st.users["u1"].rice == 14  # 新奖励进入白饭钱包
    assert st.users["u1"].snacks == 5  # 旧海苔库存保留
    assert len(st.archive) == 1  # 图鉴保留
    assert st.daily_diary is None
    assert st.minigames_unlocked is False


def test_archive_keeps_only_the_latest_twenty_entries():
    pond, _ = make_pond()
    for index in range(25):
        pond.state.status = "alive"
        pond.state.fish.name = f"鱼{index}"
        pond._end("runaway", pond.clock(), force_grade="C")
    assert len(pond.state.archive) == 20
    assert [entry["fish_name"] for entry in pond.state.archive] == [f"鱼{i}" for i in range(5, 25)]
    restored = PondState.from_dict({"archive": [{"fish_name": f"旧鱼{i}"} for i in range(25)]})
    assert [entry["fish_name"] for entry in restored.archive] == [f"旧鱼{i}" for i in range(5, 25)]


def test_restart_refuse_when_alive():
    pond, _ = make_pond()
    r = pond.handle("u1", "小明", "再养一条")
    assert not r.ok


def test_runaway_by_hunger():
    pond, clock = make_pond()
    pond.state.fish.satiety = 5.0
    clock.advance_hours(40)  # 饱腹自然归零
    pond.handle("u1", "小明", "养鱼")  # 归零计时开始
    clock.advance_hours(37)  # 超过 36h 宽限
    r = pond.handle("u1", "小明", "养鱼")
    assert pond.state.status == "ended"
    assert pond.state.end_reason == "runaway"
    assert pond.state.end_summary["grade"] == "C"
    assert r.event == "runaway"


# ---------- 指令面 ----------
def test_alias_and_unknown():
    pond, _ = make_pond()
    assert pond.handle("u1", "小明", "排行榜").title == "贡献排行"
    assert pond.handle("u1", "小明", "图鉴").title == "出栏图鉴"
    assert pond.handle("u1", "小明", "瞎搞").title == "帮助"


def test_community_exp_and_rank():
    pond, _ = make_pond()
    pond.handle("u1", "小明", "投喂")
    pond.handle("u2", "小红", "逗鱼")
    assert pond.state.community_exp == 2
    r = pond.handle("u1", "小明", "排行")
    assert any("小明" in ln for ln in r.lines)


# ---------- 名号登记 ----------
def test_nickname_gate_then_register():
    """无平台昵称的新鱼友：照料被引导登记 → 昵称登记后放行，排行显示名号。"""
    pond, _ = make_pond()
    r = pond.handle("u9", "", "投喂")  # 无平台昵称
    assert not r.ok and r.event == "need_nick"
    assert any("昵称" in ln for ln in r.lines)
    r2 = pond.handle("u9", "", "昵称", "阿鱼")
    assert r2.ok and any("阿鱼" in ln for ln in r2.lines)
    assert pond.state.users["u9"].nick_set
    r3 = pond.handle("u9", "", "投喂")
    assert r3.ok  # 登记后放行
    pond.handle("u1", "小明", "逗鱼")
    rk = pond.handle("u9", "", "排行")
    assert any("阿鱼" in ln for ln in rk.lines)


def test_nickname_rename_and_limit():
    pond, _ = make_pond()
    assert pond.handle("u1", "小明", "昵称", "大明").ok  # 平台昵称也可改名
    r = pond.handle("u1", "", "昵称", "这个名字超过了十二个字的限制")  # 16 字
    assert not r.ok
    r2 = pond.handle("u1", "", "昵称")  # 缺参 → 提示当前名号
    assert not r2.ok and any("当前名号" in ln for ln in r2.lines)
    u = pond.state.users["u1"]
    assert u.nick_set and u.display_name == "大明"


def test_platform_name_autoregisters():
    """平台带真实昵称 → 自动登记，不引导。"""
    pond, _ = make_pond()
    r = pond.handle("u1", "小明", "投喂")
    assert r.ok
    assert pond.state.users["u1"].nick_set
    r2 = pond.handle("u7", "小红", "逗鱼")
    assert r2.ok


def test_panel_nick_gate():
    """未登记名号的用户发「养鱼」→ 面板先引导登记；登记后正常出面板。"""
    pond, _ = make_pond()
    r = pond.handle("u9", "", "养鱼")
    assert not r.ok and r.event == "need_nick"
    assert any("昵称" in ln for ln in r.lines)
    pond.handle("u9", "", "昵称", "阿鱼")
    r2 = pond.handle("u9", "", "养鱼")
    assert r2.ok and r2.event == "panel"
    assert r2.data["nick"] == "阿鱼" and r2.data["nick_set"]
