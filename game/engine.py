"""《肥鱼养成记》游戏引擎。

纯逻辑模块：不 import 任何机器人框架；时钟与随机数均可注入，
单元测试与终端模拟器直接复用。

一座鱼塘（Pond）= 一个会话（群/私聊）共养的一条鱼。
并发约定：插件层需保证同一会话串行调用（如 asyncio 锁）。
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from . import actions, growth
from .config import FISH_NAMES, STAGE_NAMES, GameConfig
from .state import FishState, PondState, UserInfo

# 指令名 -> 处理器（不含机器人唤醒前缀；插件层负责解析）
COMMANDS: Dict[str, str] = {
    "养鱼": "panel", "面板": "panel",
    "投喂": "feed",
    "加餐": "snack",
    "逗鱼": "pet",
    "净水": "clean",
    "玩耍": "play",
    "称重": "weigh",
    "排行": "rank", "排行榜": "rank",
    "档案": "archive", "图鉴": "archive",
    "出栏": "graduate",
    "再养一条": "restart",
    "帮助": "help",
}

HELP_LINES = (
    "【肥鱼养成记】全群共养一条鱼，把它喂成「大肥鱼」再风光出栏！",
    "· 投喂/加餐补饱腹，逗鱼补心情，净水补洁净，玩耍赚体重但耗体力",
    "· 属性随现实时间自然衰减；同一动作连用收益递减，要换着花样来",
    "· 每日首次互动领海苔片补贴；毕业出栏按贡献发大奖励",
    "· 体重≥80 且心情≥60 且养成「大肥鱼」即可出栏结算（S/A/B/C 评级）",
    "· 饿太久或闷太久它会离家出走——记得常回来看看",
)

END_HINT = {
    "graduated": "大肥鱼已经风光出栏啦。发「再养一条」开始新的养成！",
    "runaway": "这条鱼不辞而别了……发「再养一条」重新开始。",
}

PERIOD_FLAVOR = {
    "清晨": "水面泛着薄薄的金光，它迎着第一缕光游了个早泳。",
    "上午": "阳光晒得水面暖洋洋的，泡泡一串串往上冒。",
    "中午": "它趴在浅水区打了个长长的哈欠。",
    "下午": "水面漂着云的影子，它追着影子啃了两口。",
    "傍晚": "晚霞把水染成了橘子汽水色。",
    "晚上": "水里静悄悄，只剩它吐泡泡的声音。",
    "深夜": "月亮倒映在水里，被它一尾巴拍碎。",
}


@dataclass
class Button:
    """按钮：label 是显示文字，command 是点击后发出的指令文本。"""
    label: str
    command: str


@dataclass
class ActionResult:
    ok: bool = True
    event: str = "message"   # message/panel/game_open/levelup/graduated/runaway/blocked/restart
    title: str = ""
    lines: List[str] = field(default_factory=list)
    deltas: Dict[str, float] = field(default_factory=dict)   # 显示名 -> 变化量
    buttons: List[Button] = field(default_factory=list)
    data: Dict = field(default_factory=dict)


def _period(now_ts: float) -> Tuple[str, str]:
    h = time.localtime(now_ts).tm_hour
    if 5 <= h < 10:
        return "清晨", PERIOD_FLAVOR["清晨"]
    if 10 <= h < 12:
        return "上午", PERIOD_FLAVOR["上午"]
    if 12 <= h < 14:
        return "中午", PERIOD_FLAVOR["中午"]
    if 14 <= h < 18:
        return "下午", PERIOD_FLAVOR["下午"]
    if 18 <= h < 20:
        return "傍晚", PERIOD_FLAVOR["傍晚"]
    if 20 <= h < 23:
        return "晚上", PERIOD_FLAVOR["晚上"]
    return "深夜", PERIOD_FLAVOR["深夜"]


def _note(offline: Dict[str, float], label: str, delta: float) -> None:
    """把衰减记进面板用的"离线变化"，小于 0.5 的抖动忽略。"""
    if abs(delta) >= 0.5:
        offline[label] = round(offline.get(label, 0.0) + delta, 1)


class Pond:
    """一座鱼塘。"""

    def __init__(self, state: PondState, config: Optional[GameConfig] = None,
                 clock: Optional[Callable[[], float]] = None,
                 rng: Optional[random.Random] = None,
                 store: Optional[object] = None):
        self.state = state
        self.cfg: GameConfig = config or GameConfig()
        self.clock: Callable[[], float] = clock or time.time
        self.rng: random.Random = rng or random.Random()
        self.store = store            # 传入则每次 handle 后自动落盘（鸭子类型，有 .save 即可）

    # -------------------------------------------------- 构造
    @classmethod
    def create(cls, session_key: str, config: Optional[GameConfig] = None,
               clock: Optional[Callable[[], float]] = None,
               rng: Optional[random.Random] = None,
               store: Optional[object] = None) -> "Pond":
        cfg = config or GameConfig()
        clock = clock or time.time
        rng = rng or random.Random()
        now = clock()
        fish = FishState(
            name=actions.choice(rng, FISH_NAMES),
            weight=cfg.start_weight,
            satiety=cfg.start_satiety,
            mood=cfg.start_mood,
            clean=cfg.start_clean,
            energy=cfg.start_energy,
            born_at=now,
            last_tick=now,
        )
        return cls(PondState(session_key=session_key, fish=fish), cfg, clock, rng, store)

    # -------------------------------------------------- 对外入口
    def handle(self, user_id: str, display_name: str, command: str,
               arg: Optional[str] = None) -> ActionResult:
        handler_key = COMMANDS.get((command or "").strip())
        if handler_key is None:
            result = self._do_help()
        else:
            result = getattr(self, f"_do_{handler_key}")(user_id, display_name, arg)
        if self.store is not None:
            self.store.save(self.state)
        return result

    def pond_level(self) -> int:
        step = self.cfg.pond_level_exp_step
        if step <= 0:
            return 0
        return min(self.cfg.pond_level_max, self.state.community_exp // step)

    # -------------------------------------------------- 时间推进
    def _touch(self) -> Dict[str, float]:
        """推进自然衰减，检查离家出走与玩耍超时。返回这段时间的属性变化。"""
        st = self.state
        fish = st.fish
        now = self.clock()
        offline: Dict[str, float] = {}
        if st.status == "alive":
            dt_h = max(0.0, (now - fish.last_tick) / 3600.0)
            if dt_h > 0:
                mult = growth.decay_multiplier(
                    growth.level_from_exp(fish.exp), self.pond_level(), self.cfg)
                b = fish.satiety
                fish.satiety = actions.clamp(fish.satiety - self.cfg.decay_satiety * dt_h * mult)
                _note(offline, "饱腹", fish.satiety - b)
                b = fish.mood
                fish.mood = actions.clamp(fish.mood - self.cfg.decay_mood * dt_h * mult)
                _note(offline, "心情", fish.mood - b)
                b = fish.clean
                fish.clean = actions.clamp(fish.clean - self.cfg.decay_clean * dt_h * mult)
                _note(offline, "洁净", fish.clean - b)
                b = fish.energy
                fish.energy = actions.clamp(fish.energy + self.cfg.regen_energy * dt_h)
                _note(offline, "活力", fish.energy - b)
        fish.last_tick = now
        if st.status == "alive":
            if fish.satiety <= 0:
                if fish.hunger_zero_since is None:
                    fish.hunger_zero_since = now
            else:
                fish.hunger_zero_since = None
            if fish.mood <= 0:
                if fish.mood_zero_since is None:
                    fish.mood_zero_since = now
            else:
                fish.mood_zero_since = None
            if fish.hunger_zero_since is not None and \
                    now - fish.hunger_zero_since >= self.cfg.hunger_zero_grace_hours * 3600:
                self._end("runaway", now, force_grade="C")
            elif fish.mood_zero_since is not None and \
                    now - fish.mood_zero_since >= self.cfg.mood_zero_grace_hours * 3600:
                self._end("runaway", now, force_grade="C")
        if st.pending_play and \
                now - st.pending_play.get("opened_at", 0) > self.cfg.play.pending_timeout_min * 60:
            st.pending_play = None
        return offline

    # -------------------------------------------------- 小工具
    def _user(self, uid: str, name: str) -> UserInfo:
        u = self.state.users.get(uid)
        if u is None:
            u = UserInfo(display_name=name or "匿名鱼友", snacks=self.cfg.start_snacks)
            self.state.users[uid] = u
        elif name and name != u.display_name:
            u.display_name = name
        return u

    def _top_keeper(self) -> Optional[Tuple[str, int]]:
        best = None
        for u in self.state.users.values():
            if u.intimacy > 0 and (best is None or u.intimacy > best[1]):
                best = (u.display_name or "匿名鱼友", u.intimacy)
        return best

    def _eligible(self) -> bool:
        fish = self.state.fish
        return (growth.level_from_exp(fish.exp) >= len(STAGE_NAMES)
                and fish.weight >= self.cfg.graduate_weight
                and fish.mood >= self.cfg.graduate_mood)

    def _main_buttons(self) -> List[Button]:
        btns = [
            Button("投喂", "养鱼 投喂"), Button("加餐", "养鱼 加餐"), Button("逗鱼", "养鱼 逗鱼"),
            Button("净水", "养鱼 净水"), Button("玩耍", "养鱼 玩耍"), Button("称重", "养鱼 称重"),
            Button("养鱼", "养鱼"), Button("排行", "养鱼 排行"), Button("档案", "养鱼 档案"),
        ]
        if self._eligible():
            btns.append(Button("出栏", "养鱼 出栏"))
        return btns

    def _end_buttons(self) -> List[Button]:
        return [Button("再养一条", "养鱼 再养一条"),
                Button("档案", "养鱼 档案"), Button("养鱼", "养鱼")]

    def _ended_gate(self) -> Optional[ActionResult]:
        if self.state.status == "ended":
            r = ActionResult(ok=False, event="blocked", title="这段故事已经完结",
                             lines=[END_HINT.get(self.state.end_reason or "", END_HINT["runaway"])])
            r.buttons = self._end_buttons()
            return r
        return None

    def _care_gate(self, uid: str, name: str) -> Tuple[Optional[UserInfo], Optional[ActionResult]]:
        self._touch()
        gate = self._ended_gate()
        if gate is not None:
            return None, gate
        return self._user(uid, name), None

    def _cooldown_minutes(self) -> Dict[str, int]:
        c = self.cfg
        return {"feed": c.feed.cooldown_min, "snack": c.snack.cooldown_min,
                "pet": c.pet.cooldown_min, "clean": c.clean.cooldown_min,
                "play": c.play.cooldown_min, "weigh": c.weigh.cooldown_min}

    def _cooldown_left(self, key: str, minutes: int) -> float:
        if minutes <= 0:
            return 0.0
        last = self.state.fish.cooldowns.get(key, 0.0)
        return max(0.0, minutes * 60 - (self.clock() - last))

    def _cd_busy(self, label: str, remain_sec: float) -> ActionResult:
        r = ActionResult(ok=False, title="稍等一下",
                         lines=[f"「{label}」还在休息，剩约 {max(1, math.ceil(remain_sec / 60))} 分钟。"])
        r.buttons = self._main_buttons()
        return r

    def _repeat_mult(self, key: str) -> float:
        fish = self.state.fish
        same = fish.last_action == key
        return actions.repeat_multiplier(self.cfg, fish.repeat_count if same else 0)

    def _commit(self, user: UserInfo, key: str, deltas: Dict[str, float], lines: List[str],
                exp_gain: int, title: str, event: str = "message") -> ActionResult:
        """照料动作的公共收尾：冷却/递减/群进度/亲密/补贴/升级/按钮。"""
        fish = self.state.fish
        now = self.clock()
        if self._cooldown_minutes().get(key, 0):
            fish.cooldowns[key] = now
        if fish.last_action == key:
            fish.repeat_count += 1
        else:
            fish.last_action, fish.repeat_count = key, 1
        self.state.community_exp += 1
        user.total_interactions += 1
        user.intimacy += 1
        deltas["亲密"] = deltas.get("亲密", 0.0) + 1.0
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        if user.last_bonus_day != day:
            user.last_bonus_day = day
            user.snacks += self.cfg.daily_bonus_snacks
            deltas["海苔片"] = deltas.get("海苔片", 0.0) + self.cfg.daily_bonus_snacks
            lines.append(f"顺手领了今日喂鱼补贴：海苔片 +{self.cfg.daily_bonus_snacks}")
        lv_before = growth.level_from_exp(fish.exp)
        if exp_gain:
            fish.exp += int(exp_gain)
            deltas["经验"] = float(exp_gain)
        lv_after = growth.level_from_exp(fish.exp)
        if lv_after > lv_before:
            lines.append(f"叮！进阶成「{growth.stage_name(lv_after)}」啦！")
            event = "levelup"
        if self._eligible():
            lines.append("它已经养成了大肥鱼！随时可以「出栏」毕业。")
        r = ActionResult(ok=True, event=event, title=title,
                         lines=lines, deltas={k: round(v, 1) for k, v in deltas.items()})
        r.buttons = self._main_buttons()
        return r

    # -------------------------------------------------- 面板
    def _do_panel(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        offline = self._touch()
        st = self.state
        if st.status == "ended":
            return self._settlement_result()
        fish = st.fish
        lv = growth.level_from_exp(fish.exp)
        stage = growth.stage_name(lv)
        cur, need = growth.exp_progress(fish.exp)
        pname, pflavor = _period(self.clock())
        step = self.cfg.pond_level_exp_step
        keeper = self._top_keeper()
        r = ActionResult(event="panel", title=f"{fish.name} · {stage}", lines=[pflavor])
        r.data = {
            "name": fish.name, "level": lv, "stage": stage,
            "exp_cur": cur, "exp_need": need,
            "satiety": round(fish.satiety), "mood": round(fish.mood),
            "clean": round(fish.clean), "energy": round(fish.energy),
            "weight": round(fish.weight, 1),
            "period": pname,
            "pond_level": self.pond_level(),
            "pond_exp_cur": st.community_exp % step if step else 0,
            "pond_exp_need": step,
            "offline": offline,
            "top_keeper": keeper[0] if keeper else None,
            "top_intimacy": keeper[1] if keeper else 0,
            "eligible": self._eligible(),
        }
        r.buttons = self._main_buttons()
        return r

    # -------------------------------------------------- 照料动作
    def _do_feed(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        user, gate = self._care_gate(uid, name)
        if gate is not None:
            return gate
        f = self.cfg.feed
        fish = self.state.fish
        remain = self._cooldown_left("feed", f.cooldown_min)
        if remain:
            return self._cd_busy("投喂", remain)
        deltas: Dict[str, float] = {}
        lines: List[str] = []
        if fish.satiety >= f.overfeed_threshold:
            fish.weight = max(0.0, fish.weight - f.overfeed_weight_loss)
            fish.mood = actions.clamp(fish.mood - f.overfeed_mood_loss)
            deltas["体重"] = -f.overfeed_weight_loss
            deltas["心情"] = -f.overfeed_mood_loss
            lines.append(actions.choice(self.rng, actions.FEED_OVER))
            exp_gain = f.overfeed_exp
        else:
            mult = self._repeat_mult("feed")
            before = fish.satiety
            fish.satiety = actions.clamp(fish.satiety + f.satiety_gain * mult)
            deltas["饱腹"] = fish.satiety - before
            before_e = fish.energy
            fish.energy = actions.clamp(fish.energy + f.energy_gain)
            deltas["活力"] = fish.energy - before_e
            dirty = fish.clean < self.cfg.dirty_clean_threshold
            wg = round(self.rng.uniform(f.weight_min, f.weight_max) * mult
                       * (self.cfg.dirty_growth_penalty if dirty else 1.0), 1)
            fish.weight = min(self.cfg.max_weight, fish.weight + wg)
            deltas["体重"] = wg
            lines.append(actions.choice(self.rng, actions.FEED_OK))
            if dirty:
                lines.append("（水有点脏，它胃口打了对折——先「净水」吧）")
            exp_gain = f.exp
        return self._commit(user, "feed", deltas, lines, exp_gain, title="投喂")

    def _do_snack(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        user, gate = self._care_gate(uid, name)
        if gate is not None:
            return gate
        s = self.cfg.snack
        remain = self._cooldown_left("snack", s.cooldown_min)
        if remain:
            return self._cd_busy("加餐", remain)
        if user.snacks < s.cost:
            r = ActionResult(ok=False, title="库存不足",
                             lines=["海苔片不够了……每日互动补贴和毕业奖励都能攒。"])
            r.buttons = self._main_buttons()
            return r
        user.snacks -= s.cost
        mult = self._repeat_mult("snack")
        fish = self.state.fish
        bs, bm = fish.satiety, fish.mood
        fish.satiety = actions.clamp(fish.satiety + s.satiety_gain * mult)
        fish.mood = actions.clamp(fish.mood + s.mood_gain * mult)
        deltas = {"饱腹": fish.satiety - bs, "心情": fish.mood - bm, "海苔片": -float(s.cost)}
        lines = [actions.choice(self.rng, actions.SNACK_OK)]
        return self._commit(user, "snack", deltas, lines, s.exp, title="加餐")

    def _do_pet(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        user, gate = self._care_gate(uid, name)
        if gate is not None:
            return gate
        p = self.cfg.pet
        remain = self._cooldown_left("pet", p.cooldown_min)
        if remain:
            return self._cd_busy("逗鱼", remain)
        mult = self._repeat_mult("pet")
        fish = self.state.fish
        bm = fish.mood
        fish.mood = actions.clamp(fish.mood + p.mood_gain * mult)
        deltas = {"心情": fish.mood - bm}
        lines = [actions.choice(self.rng, actions.PET_OK)]
        return self._commit(user, "pet", deltas, lines, p.exp, title="逗鱼")

    def _do_clean(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        user, gate = self._care_gate(uid, name)
        if gate is not None:
            return gate
        c = self.cfg.clean
        remain = self._cooldown_left("clean", c.cooldown_min)
        if remain:
            return self._cd_busy("净水", remain)
        mult = self._repeat_mult("clean")
        fish = self.state.fish
        bc = fish.clean
        fish.clean = actions.clamp(fish.clean + c.clean_gain * mult)
        deltas = {"洁净": fish.clean - bc}
        lines = [actions.choice(self.rng, actions.CLEAN_OK)]
        return self._commit(user, "clean", deltas, lines, c.exp, title="净水")

    def _do_play(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        user, gate = self._care_gate(uid, name)
        if gate is not None:
            return gate
        p = self.cfg.play
        fish = self.state.fish
        if arg is not None and arg.strip():
            return self._play_pick(user, arg.strip())
        remain = self._cooldown_left("play", p.cooldown_min)
        if remain:
            return self._cd_busy("玩耍", remain)
        if fish.energy < p.vitality_cost:
            r = ActionResult(ok=False, title="游不动了",
                             lines=[f"体力只剩 {fish.energy:.0f}，玩套圈要 {p.vitality_cost:.0f}。先「投喂」补补体力。"])
            r.buttons = self._main_buttons()
            return r
        self.state.pending_play = {
            "hidden": self.rng.randrange(3),
            "opened_by": uid,
            "opened_by_name": user.display_name,
            "opened_at": self.clock(),
        }
        fish.cooldowns["play"] = self.clock()
        r = ActionResult(event="game_open", title="套圈小游戏",
                         lines=[actions.choice(self.rng, actions.PLAY_OPEN)])
        r.buttons = [Button("泡泡①", "养鱼 玩耍 1"), Button("泡泡②", "养鱼 玩耍 2"),
                     Button("泡泡③", "养鱼 玩耍 3"), Button("养鱼", "养鱼")]
        r.data = {"game": "shells", "opened_by": user.display_name}
        return r

    def _play_pick(self, user: UserInfo, arg: str) -> ActionResult:
        p = self.cfg.play
        pending = self.state.pending_play
        if not pending:
            r = ActionResult(ok=False, title="没有开局",
                             lines=["现在没有进行中的套圈。先发「玩耍」开局。"])
            r.buttons = self._main_buttons()
            return r
        if arg in ("1", "2", "3"):
            n = int(arg)
        elif arg in ("①", "②", "③"):
            n = "①②③".index(arg) + 1
        else:
            r = ActionResult(ok=False, title="没听懂", lines=["要猜泡泡编号：1 / 2 / 3。"])
            r.buttons = self._main_buttons()
            return r
        self.state.pending_play = None
        fish = self.state.fish
        deltas: Dict[str, float] = {}
        lines: List[str] = []
        if n - 1 == pending["hidden"]:
            if fish.clean < self.cfg.dirty_clean_threshold:
                fish.mood = actions.clamp(fish.mood + p.fail_mood)
                deltas["心情"] = p.fail_mood
                lines.append(actions.choice(self.rng, actions.PLAY_DIRTY))
                lines.append("（水太脏，鱼粮都馊了——「净水」之后再来）")
            else:
                fish.weight = min(self.cfg.max_weight, fish.weight + p.success_weight)
                bm = fish.mood
                fish.mood = actions.clamp(fish.mood + p.success_mood)
                deltas["体重"] = p.success_weight
                deltas["心情"] = fish.mood - bm
                lines.append(actions.choice(self.rng, actions.PLAY_WIN))
        else:
            fish.mood = actions.clamp(fish.mood + p.fail_mood)
            deltas["心情"] = p.fail_mood
            lines.append(actions.choice(self.rng, actions.PLAY_MISS))
            lines.append(f"（鱼粮其实藏在 {pending['hidden'] + 1} 号泡泡）")
        return self._commit(user, "play", deltas, lines, p.exp, title="套圈")

    def _do_weigh(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        user, gate = self._care_gate(uid, name)
        if gate is not None:
            return gate
        w = self.cfg.weigh
        remain = self._cooldown_left("weigh", w.cooldown_min)
        if remain:
            return self._cd_busy("称重", remain)
        fish = self.state.fish
        lv = growth.level_from_exp(fish.exp)
        cur, need = growth.exp_progress(fish.exp)
        exp_txt = f"{cur}/{need}" if need else f"{cur}（已满阶）"
        filled = int(fish.weight // 10)
        bar = "▰" * filled + "▱" * (10 - filled)
        lines = [f"当前体重：{bar} {fish.weight:.1f} / {self.cfg.max_weight:.0f}",
                 f"成长：Lv.{lv} {growth.stage_name(lv)} · 经验 {exp_txt}"]
        tips = []
        if fish.clean < self.cfg.dirty_clean_threshold:
            tips.append("水太脏，增重减半，先「净水」。")
        if fish.mood < 40:
            tips.append("它有点闷闷不乐，多「逗鱼」。")
        if fish.energy < self.cfg.play.vitality_cost:
            tips.append("体力不足，玩耍前先「投喂」。")
        if not tips:
            tips.append("状态不错，保持节奏！")
        lines += tips
        return self._commit(user, "weigh", {}, lines, w.exp, title="称重")

    # -------------------------------------------------- 群玩法
    def _do_rank(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        self._touch()
        users = sorted((u for u in self.state.users.values() if u.intimacy > 0),
                       key=lambda u: -u.intimacy)[:5]
        lines = []
        if not users:
            lines.append("还没有鱼友照顾过它。发「投喂」抢先登榜！")
        medals = ["🥇", "🥈", "🥉", "4.", "5."]
        for i, u in enumerate(users):
            lines.append(f"{medals[i]} {u.display_name} · 亲密 {u.intimacy} · 累计 {u.total_interactions} 次")
        r = ActionResult(title="贡献排行", lines=lines)
        r.buttons = self._main_buttons()
        return r

    def _do_archive(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        self._touch()
        arc = self.state.archive
        lines = []
        if not arc:
            lines.append("图鉴还是空的。把鱼养成大肥鱼并「出栏」，就能在这里留名！")
        else:
            lines.append(f"已收录 {len(arc)} 条档案（最近 8 条）：")
            for e in arc[-8:]:
                mark = "🏆" if e.get("reason") == "graduated" else "💔"
                lines.append(f"{mark} {e.get('fish_name', '?')} · {e.get('grade', 'C')} 级 · "
                             f"体重 {e.get('weight', 0)} · 相伴 {e.get('days', 0)} 天")
        r = ActionResult(title="出栏图鉴", lines=lines)
        r.buttons = self._main_buttons()
        return r

    # -------------------------------------------------- 结束与重开
    def _do_graduate(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        self._touch()
        if self.state.status == "ended":
            return self._settlement_result()
        miss = growth.graduate_missing(self.state.fish, self.cfg)
        if miss:
            r = ActionResult(ok=False, title="还差一点",
                             lines=["出栏条件未满足：" + "、".join(miss), "再养一养吧。"])
            r.buttons = self._main_buttons()
            return r
        self._end("graduated", self.clock())
        return self._settlement_result()

    def _do_restart(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        self._touch()
        if self.state.status != "ended":
            r = ActionResult(ok=False, title="还活着呢",
                             lines=["这条鱼还活蹦乱跳，可不能说换就换～"])
            r.buttons = self._main_buttons()
            return r
        now = self.clock()
        cfg = self.cfg
        fish = FishState(
            name=actions.choice(self.rng, FISH_NAMES),
            weight=cfg.start_weight,
            satiety=cfg.start_satiety, mood=cfg.start_mood,
            clean=cfg.start_clean, energy=cfg.start_energy,
            born_at=now, last_tick=now,
        )
        self.state.fish = fish
        self.state.status = "alive"
        self.state.end_reason = None
        self.state.end_at = None
        self.state.end_summary = None
        self.state.pending_play = None
        for u in self.state.users.values():
            u.intimacy = 0     # 亲密跟着鱼走；海苔片和累计次数保留
        r = ActionResult(event="restart", title="新的一生",
                         lines=[actions.choice(self.rng, actions.NEW_FISH),
                                f"这一尾叫「{fish.name}」。档案和海苔片都还在，接着加油！"])
        r.buttons = self._main_buttons()
        return r

    def _do_help(self, uid: Optional[str] = None, name: Optional[str] = None,
                 arg: Optional[str] = None) -> ActionResult:
        r = ActionResult(title="帮助", lines=list(HELP_LINES))
        r.buttons = self._main_buttons()
        return r

    # -------------------------------------------------- 结算
    def _end(self, reason: str, now: float, force_grade: Optional[str] = None) -> None:
        fish = self.state.fish
        keeper = self._top_keeper()
        score_val = growth.score(fish, keeper[1] if keeper else 0.0, self.cfg)
        grade_val = force_grade or growth.grade(score_val, self.cfg)
        days = round(max(0.0, now - fish.born_at) / 86400.0, 1)
        rewards: List[Tuple[str, int]] = []
        if reason == "graduated":
            ranked = sorted((u for u in self.state.users.values() if u.intimacy > 0),
                            key=lambda u: -u.intimacy)
            base = growth.reward_for_grade(grade_val, self.cfg)
            if ranked and base > 0:
                first = ranked[0]
                first.snacks += base
                rewards.append((first.display_name or "匿名鱼友", base))
                half = base // 2
                for u in ranked[1:3]:
                    if half > 0:
                        u.snacks += half
                        rewards.append((u.display_name or "匿名鱼友", half))
        self.state.end_summary = {
            "reason": reason, "fish_name": fish.name, "grade": grade_val, "score": score_val,
            "weight": round(fish.weight, 1), "mood": round(fish.mood), "clean": round(fish.clean),
            "days": days,
            "top_keeper": keeper[0] if keeper else None,
            "top_intimacy": keeper[1] if keeper else 0,
            "rewards": rewards, "ended_at": now,
        }
        self.state.end_reason = reason
        self.state.end_at = now
        self.state.status = "ended"
        self.state.pending_play = None
        self.state.archive.append({
            "fish_name": fish.name, "reason": reason, "grade": grade_val, "score": score_val,
            "weight": round(fish.weight, 1), "days": days, "ended_at": now,
        })

    def _settlement_result(self) -> ActionResult:
        s = self.state.end_summary or {}
        if self.state.end_reason == "graduated":
            lines = [
                f"锣声一响，「{s.get('fish_name', '')}」风光出栏！",
                f"综合评分 {s.get('score', 0)} —— 评级 {s.get('grade', 'C')}",
                f"体重 {s.get('weight', 0)} · 心情 {s.get('mood', 0)} · 洁净 {s.get('clean', 0)}",
                f"相伴 {s.get('days', 0)} 天 · 最上心的鱼友：{s.get('top_keeper') or '暂无'}",
            ]
            for name, amount in s.get("rewards") or []:
                lines.append(f"感谢 {name} 的悉心照料：海苔片 +{amount}")
            lines.append("它游进了更宽阔的池塘，档案已收录进图鉴。")
            event, title = "graduated", "出栏大典"
        else:
            lines = [
                f"饿了太久，「{s.get('fish_name', '')}」收拾小包袱游向了大海……",
                f"综合评分 {s.get('score', 0)} —— 评级 C（遗憾收场）",
                f"相伴 {s.get('days', 0)} 天。下次记得常回家看看。",
            ]
            event, title = "runaway", "不辞而别"
        r = ActionResult(event=event, title=title, lines=lines)
        r.data = {"summary": s, "status": "ended"}
        r.buttons = self._end_buttons()
        return r
