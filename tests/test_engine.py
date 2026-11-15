"""引擎核心循环 / 成长 / 结算 / 重开 / 衰减 的单元测试。

时钟与随机数全部注入，测试完全确定。
"""
import random

import pytest

from game import GameConfig, Pond
from game import growth
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
    return Pond.create("test:group:1", config=cfg, clock=clock, rng=rng), clock


# ---------- 基础动作 ----------
def test_feed_basic():
    pond, clock = make_pond()
    r = pond.handle("u1", "小明", "投喂")
    assert r.ok
    assert r.deltas["饱腹"] == pytest.approx(22.0)
    assert 1.5 <= r.deltas["体重"] <= 3.5
    assert r.deltas["经验"] == 8
    assert pond.state.fish.cooldowns["feed"] == clock.now
    r2 = pond.handle("u1", "小明", "投喂")          # 冷却内被拒
    assert not r2.ok
    clock.advance_hours(0.75)
    r3 = pond.handle("u1", "小明", "投喂")
    assert r3.ok


def test_pet_clean_snack():
    pond, clock = make_pond()
    r = pond.handle("u1", "小明", "逗鱼")
    assert r.deltas["心情"] == pytest.approx(6.0)
    assert r.deltas["亲密"] == 1
    pond.state.fish.clean = 20.0        # 压低起点，避免 80+35 触顶截断
    clock.advance_hours(0.3)
    r = pond.handle("u1", "小明", "净水")
    assert r.deltas["洁净"] == pytest.approx(35.0)
    clock.advance_hours(0.3)
    r = pond.handle("u1", "小明", "加餐")           # 海苔片 -1，今日补贴已领
    assert r.deltas["海苔片"] == pytest.approx(-1.0)
    assert pond.state.users["u1"].snacks == 3       # 3(初始) +1(补贴) -1(加餐)


def test_snack_refuse_when_empty():
    pond, _ = make_pond()
    pond.state.users["u9"] = UserInfo(display_name="阿九", snacks=0)
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
    assert fish.satiety == pytest.approx(95.0)      # 撑到不涨饱腹
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
    assert r1.deltas["心情"] == pytest.approx(6.0)
    clock.advance(15 * 60 + 1)
    r2 = pond.handle("u1", "小明", "逗鱼")
    assert r2.deltas["心情"] == pytest.approx(4.5)      # ×0.75
    clock.advance(15 * 60 + 1)
    r3 = pond.handle("u1", "小明", "逗鱼")
    # ×0.75² = 3.375，展示层四舍五入到 1 位小数
    assert r3.deltas["心情"] == pytest.approx(3.375, abs=0.05)


# ---------- 套圈小游戏 ----------
def test_play_success_and_miss():
    pond, clock = make_pond()
    r = pond.handle("u1", "小明", "玩耍")
    assert r.event == "game_open"
    assert pond.state.fish.energy == pytest.approx(60.0)    # 开局扣 20 活力
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
    assert "体重" not in r.deltas                   # 猜中也捞不到
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
    pond.handle("u1", "小明", "养鱼")               # touch 顺带清理超时
    assert pond.state.pending_play is None
    r = pond.handle("u1", "小明", "玩耍", arg="1")
    assert not r.ok


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
    pond.handle("u1", "小明", "养鱼")               # 先 touch 一次对齐时钟
    clock.advance_hours(2)
    r = pond.handle("u1", "小明", "养鱼")
    fish = pond.state.fish
    assert fish.satiety == pytest.approx(70 - 2.5 * 2)
    assert fish.mood == pytest.approx(70 - 1.5 * 2)
    assert fish.clean == pytest.approx(80 - 1.0 * 2)
    assert fish.energy == pytest.approx(min(100.0, 80 + 6.0 * 2))
    assert r.data["offline"]["饱腹"] == pytest.approx(-5.0)


def test_decay_stage_multiplier():
    pond, clock = make_pond()
    pond.state.fish.exp = 500                       # Lv.6 → ×1.85
    clock.advance_hours(2)
    pond.handle("u1", "小明", "养鱼")
    assert pond.state.fish.satiety == pytest.approx(70 - 2.5 * 2 * 1.85)


def test_pond_level_relief():
    pond, _ = make_pond()
    pond.state.community_exp = 199
    pond.handle("u1", "小明", "逗鱼")               # 第 200 次群互动
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
    pond.state.users["u1"] = UserInfo(display_name="小明", intimacy=55)
    pond.state.users["u2"] = UserInfo(display_name="小红", intimacy=30)
    pond.state.users["u3"] = UserInfo(display_name="阿三", intimacy=20)
    r = pond.handle("u1", "小明", "出栏")
    assert r.event == "graduated"
    assert pond.state.status == "ended"
    s = pond.state.end_summary
    # 评分 = 90×0.4 + 80×0.25 + 70×0.15 + 55×0.2 = 77.5 → A
    assert s["score"] == pytest.approx(77.5)
    assert s["grade"] == "A"
    assert pond.state.archive[-1]["grade"] == "A"
    assert pond.state.users["u1"].snacks == 12      # 第 1 名全额
    assert pond.state.users["u2"].snacks == 6       # 第 2/3 名半额
    assert pond.state.users["u3"].snacks == 6
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
    pond.state.users["u1"] = UserInfo(display_name="小明", intimacy=55)
    pond.handle("u1", "小明", "出栏")
    r = pond.handle("u1", "小明", "再养一条")
    assert r.ok
    st = pond.state
    assert st.status == "alive"
    assert st.fish.weight == pond.cfg.start_weight
    assert st.fish.exp == 0
    assert st.users["u1"].intimacy == 0             # 亲密跟着鱼走
    assert st.users["u1"].snacks == 12              # 钱包保留
    assert len(st.archive) == 1                     # 图鉴保留


def test_restart_refuse_when_alive():
    pond, _ = make_pond()
    r = pond.handle("u1", "小明", "再养一条")
    assert not r.ok


def test_runaway_by_hunger():
    pond, clock = make_pond()
    pond.state.fish.satiety = 5.0
    clock.advance_hours(40)                         # 饱腹自然归零
    pond.handle("u1", "小明", "养鱼")               # 归零计时开始
    clock.advance_hours(37)                         # 超过 36h 宽限
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
