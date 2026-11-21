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
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from . import actions, growth, travel
from .adventures import STORIES
from .config import FISH_NAMES, STAGE_NAMES, GameConfig
from .decorations import DECORATIONS, completed
from .state import ARCHIVE_LIMIT, FishState, PondState, UserInfo

# 指令名 -> 处理器（不含机器人唤醒前缀；插件层负责解析）
COMMANDS: Dict[str, str] = {
    "养鱼": "panel", "面板": "panel",
    "投喂": "feed",
    "加餐": "snack",
    "逗鱼": "pet",
    "净水": "clean",
    "玩耍": "play", "Token陪玩": "play_token",
    "称重": "weigh",
    "排行": "rank", "排行榜": "rank",
    "档案": "archive", "图鉴": "archive",
    "出栏": "graduate",
    "再养一条": "restart",
    "昵称": "nickname", "改名": "nickname",
    "签到": "sign", "打卡": "sign",
    "钓鱼": "fishing",
    "救援": "rescue",
    "许愿": "wish",
    "小游戏": "games", "猜鱼": "guess", "寻宝": "treasure",
    "奇遇": "adventure", "游历": "travel", "心愿": "heartwish", "纪念物": "keepsakes",
    "建设": "build", "装修": "build", "纪念册": "memorial", "合影": "memorial",
    "运动会": "sports", "性格": "personality",
    "委托": "weekly", "周报": "weekly_reports",
    "帮助": "help",
}

HELP_LINES = (
    "【肥鱼养成记】全群共养一条鱼，把它喂成「大肥鱼」再风光出栏！",
    "· 自动采用平台昵称；未收到昵称时再登记，也可「养鱼 昵称 名字」自定义名号",
    "· 投喂/加餐补饱腹，逗鱼补心情，净水补洁净，玩耍赚体重但耗体力",
    "· 属性随现实时间自然衰减；同一动作连用收益递减，要换着花样来",
    "· 每日「签到」和「钓鱼」可获得白饭、Token；「许愿」消耗白饭换增益",
    "· 求援事件：鱼儿被困时全群点「救援」，凑齐人数有奖励，没人救它会难过",
    "· 「小游戏」查看新玩法：「猜鱼」猜数字（每人冷却30分钟）；「寻宝」三人依次探路/搬石/开箱（全塘冷却60分钟）",
    "· 「奇遇」每天每人一次，选路线收集「纪念物」；「心愿」查看今日全塘共同目标，照料时自动推进",
    "· 「建设」捐材料共建永久装饰；心愿完成时参与者、成功奇遇各得1份材料；「纪念册」翻看历史合影",
    "· 「性格」由本轮照料习惯形成；「运动会」每周参加一次，选项目比拼鱼的状态",
    "· 「委托」查看本周全塘共同目标，照料自动累计；「周报」翻看最近八期已完成记录",
    "· 毕业出栏按贡献发放奖励；体重≥80 且心情≥60 且养成「大肥鱼」即可出栏",
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
        # 求援事件过期：无人救援，鱼心情受挫
        pr = st.pending_rescue
        if pr and now > pr.get("expires", 0):
            st.pending_rescue = None
            if st.status == "alive":
                b = fish.mood
                fish.mood = actions.clamp(fish.mood - self.cfg.rescue.expire_mood_loss)
                _note(offline, "心情", fish.mood - b)
        for uid, game in list(st.pending_guess.items()):
            if now >= game["expires"]:
                del st.pending_guess[uid]
        if st.pending_treasure and now >= st.pending_treasure["expires"]:
            st.pending_treasure = None
        if st.pending_adventure and now >= st.pending_adventure["expires"]:
            st.pending_adventure = None
        if st.pending_travel and now >= st.pending_travel["expires"]:
            st.pending_travel = None
        return offline

    # -------------------------------------------------- 小工具
    def _user(self, uid: str, name: str) -> UserInfo:
        u = self.state.users.get(uid)
        if u is None:
            # 收到平台昵称直接登记，缺失时才引导。
            u = UserInfo(display_name=name or "", nick_set=bool(name),
                         rice=self.cfg.start_rice)
            self.state.users[uid] = u
        elif name and not u.nick_set:
            u.display_name = name      # 平台后来补了昵称且未主动登记 → 采用
            u.nick_set = True
        return u

    def _nick_gate(self, user: UserInfo) -> Optional[ActionResult]:
        if user.nick_set:
            return None
        r = ActionResult(ok=False, event="need_nick", title="初次见面",
                         lines=["第一次见面，先登记一下～",
                                "直接回复你的名字就行（12 字以内），",
                                "也可以发「养鱼 昵称 名字」。",
                                "之后喂养和排行榜都记在这个名字下。"])
        r.buttons = [Button("养鱼", "养鱼"), Button("帮助", "养鱼 帮助")]
        return r

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
            Button("净水", "养鱼 净水"), Button("玩耍", "养鱼 玩耍"),
            Button("Token陪玩", "养鱼 Token陪玩"), Button("称重", "养鱼 称重"),
            Button("养鱼", "养鱼"), Button("排行", "养鱼 排行"), Button("档案", "养鱼 档案"),
        ]
        pr = self.state.pending_rescue
        if self.state.status == "alive" and pr:
            btns.append(Button(f"🛟 救援({len(pr.get('got', {}))}/{pr.get('need', 0)})",
                               "养鱼 救援"))
        btns.extend([
            Button("📅 签到", "养鱼 签到"), Button("🎣 钓鱼", "养鱼 钓鱼"),
            Button("🧭 游历", "养鱼 游历"),
            Button("🙏 许愿", "养鱼 许愿"),
            Button("🎮 小游戏", "养鱼 小游戏"),
        ])
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
        user = self._user(uid, name)
        gate = self._nick_gate(user)
        if gate is not None:
            return None, gate
        self._ensure_heartwish(uid)
        return user, None

    def _cooldown_minutes(self) -> Dict[str, int]:
        c = self.cfg
        return {"feed": c.feed.cooldown_min, "snack": c.snack.cooldown_min,
                "pet": c.pet.cooldown_min, "clean": c.clean.cooldown_min,
                "play": c.play.cooldown_min, "weigh": c.weigh.cooldown_min,
                "fishing": c.fishing.cooldown_min, "treasure": c.treasure.cooldown_min}

    def _cooldown_left(self, key: str, minutes: int,
                       user: Optional[UserInfo] = None) -> float:
        if minutes <= 0:
            return 0.0
        last = self.state.fish.cooldowns.get(key, 0.0)
        if user is not None:
            last = max(last, user.cooldowns.get(key, 0.0))
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

    def _maybe_spawn_rescue(self, now: float, lines: List[str]) -> None:
        """照料动作后按概率刷出求援事件（同一时间只有一个）。"""
        rc = self.cfg.rescue
        if rc.chance <= 0 or self.state.pending_rescue is not None \
                or self.state.status != "alive":
            return
        if self.rng.random() >= rc.chance:
            return
        kind, text = actions.choice(self.rng, actions.RESCUE_KINDS)
        self.state.pending_rescue = {
            "kind": kind, "text": text, "need": rc.need, "got": {},
            "expires": now + rc.expire_min * 60,
        }
        lines.append(f"🆘 突发事件：{text}！需要 {rc.need} 位鱼友救援"
                     f"（{rc.expire_min} 分钟内点「救援」参与）")

    def _commit(self, user: UserInfo, key: str, deltas: Dict[str, float], lines: List[str],
                exp_gain: int, title: str, event: str = "message",
                intimacy_gain: float = 1.0) -> ActionResult:
        """照料动作的公共收尾：冷却/递减/群进度/亲密/补贴/升级/按钮。"""
        fish = self.state.fish
        now = self.clock()
        uid = next(k for k, u in self.state.users.items() if u is user)
        stats = self.state.round_stats.setdefault(uid, {})
        category = {"clean": "clean", "adventure": "adventure",
                    "feed": "feed", "snack": "feed", "pet": "pet", "play": "play"}.get(key)
        if category and any(deltas.get(k, 0) > 0 for k in ("饱腹", "心情", "洁净", "体重")):
            stats["care"] = stats.get("care", 0) + 1
            stats[category] = stats.get(category, 0) + 1
        self._progress_weekly(uid, key, deltas, lines)
        user.last_care_at = now
        self._progress_heartwish(user, key, deltas, lines)
        if self._cooldown_minutes().get(key, 0):
            user.cooldowns[key] = now
        if fish.last_action == key:
            fish.repeat_count += 1
        else:
            fish.last_action, fish.repeat_count = key, 1
        self.state.community_exp += 1
        user.total_interactions += 1
        if intimacy_gain:
            user.intimacy += intimacy_gain
            deltas["亲密"] = deltas.get("亲密", 0.0) + intimacy_gain
        self._maybe_spawn_rescue(now, lines)
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        if user.last_bonus_day != day:
            user.last_bonus_day = day
            user.rice += self.cfg.daily_bonus_snacks
            deltas["白饭"] = deltas.get("白饭", 0.0) + self.cfg.daily_bonus_snacks
            lines.append(f"顺手领了今日喂鱼补贴：白饭 +{self.cfg.daily_bonus_snacks}")
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
        user = self._user(uid, name)
        gate = self._nick_gate(user)
        if gate is not None:
            return gate
        fish = st.fish
        lv = growth.level_from_exp(fish.exp)
        stage = growth.stage_name(lv)
        cur, need = growth.exp_progress(fish.exp)
        pname, pflavor = _period(self.clock())
        step = self.cfg.pond_level_exp_step
        keeper = self._top_keeper()
        r = ActionResult(event="panel", title=f"{fish.name} · {stage}", lines=[pflavor])
        personality = self._personality()
        r.lines.append("性格：" + (personality or "慢慢熟悉中"))
        quest = self._ensure_weekly(uid)
        r.lines.append(f"本周委托：{'已完成' if quest['done'] else '进行中'}，发「委托」查看剩余目标。")
        wish = self._ensure_heartwish(uid)
        r.lines.append(f"💭 {wish['title']}（{len(wish['got'])}/{wish['goal']}，{'已完成' if wish['done'] else '进行中'}）")
        if st.pending_adventure:
            r.lines.append(f"🌊 {st.pending_adventure['name']}的奇遇进行中，点「小游戏 → 鱼塘奇遇」查看。")
        me = self.state.users.get(uid)
        if me:
            r.lines.append(f"你的道具：白饭 {me.rice} · Token {me.tokens} · 绝版海苔 {me.snacks}")
            r.lines.append(f"游历机会：{me.travel_tickets}/{self.cfg.sign.ticket_cap}")
        pr = st.pending_rescue
        if pr and st.status == "alive":
            r.lines.append(f"🆘 求援进行中：{pr['text']}"
                           f"（{len(pr.get('got', {}))}/{pr['need']}）点「救援」参与")
        r.data = {
            "name": fish.name, "level": lv, "stage": stage,
            "exp_cur": cur, "exp_need": need,
            "satiety": round(fish.satiety), "mood": round(fish.mood),
            "clean": round(fish.clean), "energy": round(fish.energy),
            "weight": round(fish.weight, 1),
            "max_weight": self.cfg.max_weight,
            "period": pname,
            "pond_level": self.pond_level(),
            "pond_exp_cur": st.community_exp % step if step else 0,
            "pond_exp_need": step,
            "offline": offline,
            "nick": me.display_name if me else "",
            "nick_set": bool(me.nick_set) if me else False,
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
        remain = self._cooldown_left("feed", f.cooldown_min, user)
        if remain:
            return self._cd_busy("投喂", remain)
        if user.rice < f.rice_cost:
            r = ActionResult(ok=False, title="白饭不够了",
                             lines=[f"投喂需要白饭 ×{f.rice_cost}，签到后去「游历」或「钓鱼」都能获得。"])
            r.buttons = [Button("游历", "养鱼 游历"), Button("钓鱼", "养鱼 钓鱼"), *self._main_buttons()]
            return r
        user.rice -= f.rice_cost
        deltas: Dict[str, float] = {}
        deltas["白饭"] = -float(f.rice_cost)
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
        remain = self._cooldown_left("snack", s.cooldown_min, user)
        if remain:
            return self._cd_busy("加餐", remain)
        if user.rice < s.cost:
            r = ActionResult(ok=False, title="库存不足",
                             lines=[f"白饭不够了……加餐需要白饭 {s.cost} 份，可通过签到、钓鱼和游历获得。"])
            r.buttons = self._main_buttons()
            return r
        user.rice -= s.cost
        mult = self._repeat_mult("snack")
        fish = self.state.fish
        bs, bm = fish.satiety, fish.mood
        fish.satiety = actions.clamp(fish.satiety + s.satiety_gain * mult)
        fish.mood = actions.clamp(fish.mood + s.mood_gain * mult)
        deltas = {"饱腹": fish.satiety - bs, "心情": fish.mood - bm, "白饭": -float(s.cost)}
        lines = [actions.choice(self.rng, actions.SNACK_OK)]
        return self._commit(user, "snack", deltas, lines, s.exp, title="加餐")

    def _do_pet(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        user, gate = self._care_gate(uid, name)
        if gate is not None:
            return gate
        p = self.cfg.pet
        remain = self._cooldown_left("pet", p.cooldown_min, user)
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
        remain = self._cooldown_left("clean", c.cooldown_min, user)
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
        if self.state.pending_play:
            pending = self.state.pending_play
            if pending.get("opened_by") == uid:
                buttons = [Button(f"泡泡{i}", f"养鱼 玩耍 {i}") for i in ("①", "②", "③")]
                buttons.append(Button("养鱼", "养鱼"))
            else:
                buttons = self._main_buttons()
            return ActionResult(title="套圈进行中",
                                lines=[f"{pending.get('opened_by_name', '鱼友')}正在选泡泡，先让这一局结束。"],
                                buttons=buttons)
        remain = self._cooldown_left("play", p.cooldown_min, user)
        if remain:
            return self._cd_busy("玩耍", remain)
        if fish.energy < p.vitality_cost:
            r = ActionResult(ok=False, title="游不动了",
                             lines=[f"体力只剩 {fish.energy:.0f}，玩套圈要 {p.vitality_cost:.0f}。先「投喂」补补体力。"])
            r.buttons = self._main_buttons()
            return r
        fish.energy = actions.clamp(fish.energy - p.vitality_cost)   # 开局先耗体力
        self.state.pending_play = {
            "hidden": self.rng.randrange(3),
            "opened_by": uid,
            "opened_by_name": user.display_name,
            "opened_at": self.clock(),
            "boosted": False,
        }
        user.cooldowns["play"] = self.clock()
        r = ActionResult(event="game_open", title="套圈小游戏",
                         lines=[actions.choice(self.rng, actions.PLAY_OPEN)])
        r.deltas = {"活力": -p.vitality_cost}
        r.buttons = [Button("泡泡①", "养鱼 玩耍 1"), Button("泡泡②", "养鱼 玩耍 2"),
                     Button("泡泡③", "养鱼 玩耍 3"), Button("养鱼", "养鱼")]
        r.data = {"game": "shells", "opened_by": user.display_name}
        return r

    def _do_play_token(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        user, gate = self._care_gate(uid, name)
        if gate is not None:
            return gate
        if self.state.pending_play:
            return ActionResult(ok=False, title="先完成当前玩耍",
                                lines=["一局还没结束，完成后再开 Token 陪玩。"],
                                buttons=self._main_buttons())
        if user.tokens < 1:
            return ActionResult(ok=False, title="Token 不够了",
                                lines=["Token 可以从钓鱼和游历中获得。"],
                                buttons=[Button("钓鱼", "养鱼 钓鱼"), Button("游历", "养鱼 游历")])
        result = self._do_play(uid, name, None)
        if result.event == "game_open":
            self.state.pending_play["boosted"] = True
            result.lines.insert(0, "使用 Token 陪玩：猜中后额外增加体重和心情。")
        return result

    def _play_pick(self, user: UserInfo, arg: str) -> ActionResult:
        p = self.cfg.play
        pending = self.state.pending_play
        if not pending:
            r = ActionResult(ok=False, title="没有开局",
                             lines=["现在没有进行中的套圈。先发「玩耍」开局。"])
            r.buttons = self._main_buttons()
            return r
        uid = next(k for k, candidate in self.state.users.items() if candidate is user)
        if pending.get("opened_by") and pending["opened_by"] != uid:
            return ActionResult(ok=False, title="这是别人的套圈",
                                lines=["请让开局的鱼友选择泡泡，避免替别人结算。"],
                                buttons=self._main_buttons())
        if arg in ("1", "2", "3"):
            n = int(arg)
        elif arg in ("①", "②", "③"):
            n = "①②③".index(arg) + 1
        else:
            r = ActionResult(ok=False, title="没听懂", lines=["要猜泡泡编号：1 / 2 / 3。"])
            r.buttons = self._main_buttons()
            return r
        if pending.get("boosted"):
            if user.tokens < 1:
                return ActionResult(ok=False, title="Token 不够了",
                                    lines=["这局尚未结算，Token 不足，无法使用加成。"],
                                    buttons=self._main_buttons())
            user.tokens -= 1
        self.state.pending_play = None
        fish = self.state.fish
        deltas: Dict[str, float] = {}
        lines: List[str] = []
        if pending.get("boosted"):
            deltas["Token"] = -1.0
        if n - 1 == pending["hidden"]:
            if fish.clean < self.cfg.dirty_clean_threshold:
                fish.mood = actions.clamp(fish.mood + p.fail_mood)
                deltas["心情"] = p.fail_mood
                lines.append(actions.choice(self.rng, actions.PLAY_DIRTY))
                lines.append("（水太脏，鱼粮都馊了——「净水」之后再来）")
            else:
                before_weight = fish.weight
                weight_gain = p.success_weight + (p.token_weight_bonus if pending.get("boosted") else 0)
                fish.weight = min(self.cfg.max_weight, fish.weight + weight_gain)
                bm = fish.mood
                mood_gain = p.success_mood + (p.token_mood_bonus if pending.get("boosted") else 0)
                fish.mood = actions.clamp(fish.mood + mood_gain)
                deltas["体重"] = fish.weight - before_weight
                deltas["心情"] = fish.mood - bm
                lines.append(actions.choice(self.rng, actions.PLAY_WIN))
                if pending.get("boosted"):
                    lines.append("Token 的加成生效，玩得特别尽兴！")
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
        remain = self._cooldown_left("weigh", w.cooldown_min, user)
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
        return self._commit(user, "weigh", {}, lines, w.exp, title="称重", intimacy_gain=0)

    # -------------------------------------------------- 名号登记
    def _do_nickname(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        """登记/修改名号：养鱼 昵称 名字。不算照料动作（无经验/亲密/群进度）。"""
        self._touch()
        user = self._user(uid, name)
        new = (arg or "").strip()
        if not new:
            cur = user.display_name or "（未登记）"
            r = ActionResult(ok=False, title="名号登记",
                             lines=[f"当前名号：{cur}",
                                    "发「养鱼 昵称 名字」登记或改名（不超过 12 字）。"])
            r.buttons = self._main_buttons()
            return r
        if len(new) > 12:
            r = ActionResult(ok=False, title="太长了",
                             lines=["名号最多 12 个字，换一个吧。"])
            r.buttons = self._main_buttons()
            return r
        old = user.display_name or "（未登记）"
        user.display_name = new
        user.nick_set = True
        r = ActionResult(event="nick", title="名号登记",
                         lines=[f"「{old}」→「{new}」",
                                "排行和档案以后就显示这个名字啦！"])
        r.buttons = self._main_buttons()
        return r

    # -------------------------------------------------- 玩法扩展：签到/钓鱼/救援/许愿
    def _do_sign(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        """每日打卡：每自然日一次，连续天数递进奖励白饭和 Token（用户级，跨鱼）。"""
        self._touch()
        user = self._user(uid, name)
        now = self.clock()
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        if user.last_sign_day == day:
            r = ActionResult(ok=False, title="已打卡",
                             lines=[f"今天已经打过卡啦（连续 {user.sign_streak} 天）",
                                    "明天再来～"])
            r.buttons = self._main_buttons()
            return r
        yesterday = time.strftime("%Y-%m-%d", time.localtime(now - 86400))
        streak = user.sign_streak + 1 if user.last_sign_day == yesterday else 1
        sc = self.cfg.sign
        reward = sc.base
        if streak >= 7:
            reward += sc.streak7
        elif streak >= 3:
            reward += sc.streak3
        user.last_sign_day = day
        user.sign_streak = streak
        user.rice += reward
        ticket_gain = int(user.travel_tickets < self.cfg.sign.ticket_cap)
        user.travel_tickets += ticket_gain
        token_reward = 1 if streak >= 7 else 0
        user.tokens += token_reward
        r = ActionResult(event="sign", title="每日打卡",
                         lines=[f"打卡成功，连续 {streak} 天！",
                                 f"白饭 +{reward}"] +
                                ([f"🔥 连续 {streak} 天，追加白饭 +{reward - sc.base}"]
                                if reward > sc.base else []))
        r.deltas = {"白饭": float(reward)}
        r.deltas["游历机会"] = float(ticket_gain)
        if token_reward:
            r.deltas["Token"] = float(token_reward)
            r.lines.append("连续签到奖励：Token +1")
        r.lines.append(f"游历机会：{user.travel_tickets}/{self.cfg.sign.ticket_cap}")
        r.buttons = self._main_buttons()
        return r

    def _do_fishing(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        """一键钓鱼：冷却 30 分钟，五档随机奖励（概率表见 FishingAction）。"""
        user, gate = self._care_gate(uid, name)
        if gate is not None:
            return gate
        fcfg = self.cfg.fishing
        remain = self._cooldown_left("fishing", fcfg.cooldown_min, user)
        if remain:
            return self._cd_busy("钓鱼", remain)
        roll = self.rng.random()
        deltas: Dict[str, float] = {}
        lines: List[str] = []
        cum = fcfg.p_rice
        if roll < cum:
            user.rice += 1
            deltas["白饭"] = 1.0
            lines.append("鱼钩捞起一团白饭，正好能喂大肥鱼。")
        elif roll < (cum := cum + fcfg.p_token):
            user.tokens += 1
            deltas["Token"] = 1.0
            lines.append("浮漂钓起一枚 Token，收好了！")
        elif roll < (cum := cum + fcfg.p_bundle):
            user.rice += 2
            deltas["白饭"] = 2.0
            lines.append("钓到饭盒，里面装着白饭 ×2。")
        else:
            user.rice += 3
            user.tokens += 1
            deltas["白饭"] = 3.0
            deltas["Token"] = 1.0
            lines.append(f"宝箱打开了：白饭 ×{fcfg.chest_snacks}、Token ×1！")
        return self._commit(user, "fishing", deltas, lines, fcfg.exp, title="钓鱼", intimacy_gain=0)

    def _do_rescue(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        """求援事件：全群点按钮协作，凑齐人数发奖励；过期鱼心情受挫。"""
        self._touch()
        if self.state.status == "ended":
            return self._settlement_result()
        pr = self.state.pending_rescue
        if not pr:
            r = ActionResult(ok=False, title="没有求援",
                             lines=["现在没有进行中的求援事件。",
                                    "平时照料鱼儿时可能会突发，留意群里消息～"])
            r.buttons = self._main_buttons()
            return r
        if uid in pr.get("got", {}):
            r = ActionResult(ok=False, title="已救援",
                             lines=["你已经救过一次了，把机会让给别人吧！"])
            r.buttons = self._main_buttons()
            return r
        user = self._user(uid, name)
        gate = self._nick_gate(user)
        if gate is not None:
            return gate
        rc = self.cfg.rescue
        pr.setdefault("got", {})[uid] = user.display_name or "匿名鱼友"
        fish = self.state.fish
        got_names = list(pr["got"].values())
        if len(pr["got"]) >= pr["need"]:
            for ruid, rname in pr["got"].items():
                self._user(ruid, rname).rice += rc.reward_snacks
            bm = fish.mood
            fish.mood = actions.clamp(fish.mood + rc.reward_mood)
            lv_before = growth.level_from_exp(fish.exp)
            fish.exp += 8
            lv_after = growth.level_from_exp(fish.exp)
            self.state.pending_rescue = None
            lines = [f"救援成功！{'、'.join(got_names)} 把鱼儿解救了出来。",
                     f"参与者：白饭 +{rc.reward_snacks}",
                     f"鱼儿：心情 +{rc.reward_mood:.0f} · 经验 +8"]
            if lv_after > lv_before:
                lines.append(f"叮！进阶成「{growth.stage_name(lv_after)}」啦！")
            r = ActionResult(event="rescue", title="救援成功", lines=lines,
                             deltas={"心情": fish.mood - bm, "经验": 8.0})
            r.buttons = self._main_buttons()
            return r
        r = ActionResult(event="rescue", title="求援中",
                         lines=[f"🛟 {pr['text']}——已就位 {len(pr['got'])}/{pr['need']}"
                                f"（{'、'.join(got_names)}）",
                                f"还差 {pr['need'] - len(pr['got'])} 位：点下方按钮参与！"])
        r.buttons = self._main_buttons()
        return r

    def _do_wish(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        """许愿池：花白饭随机换增益（心情/活力/体重/返还）。"""
        user, gate = self._care_gate(uid, name)
        if gate is not None:
            return gate
        w = self.cfg.wish
        fish = self.state.fish
        if user.rice < w.cost:
            r = ActionResult(ok=False, title="白饭不够",
                             lines=[f"许愿需要白饭 {w.cost} 份，你现在有 {user.rice} 份。",
                                    "签到、钓鱼、毕业奖励都能攒～"])
            r.buttons = self._main_buttons()
            return r
        user.rice -= w.cost
        deltas: Dict[str, float] = {"白饭": -float(w.cost)}
        lines: List[str] = []
        roll = self.rng.randrange(4)
        if roll == 0:
            bm = fish.mood
            fish.mood = actions.clamp(fish.mood + w.mood_gain)
            deltas["心情"] = fish.mood - bm
            lines.append(actions.choice(self.rng, actions.WISH_MOOD))
        elif roll == 1:
            be = fish.energy
            fish.energy = actions.clamp(fish.energy + w.energy_gain)
            deltas["活力"] = fish.energy - be
            lines.append(actions.choice(self.rng, actions.WISH_ENERGY))
        elif roll == 2:
            fish.weight = min(self.cfg.max_weight, fish.weight + w.weight_gain)
            deltas["体重"] = w.weight_gain
            lines.append(actions.choice(self.rng, actions.WISH_WEIGHT))
        else:
            user.rice += w.refund
            deltas["白饭"] = deltas.get("白饭", 0.0) + w.refund
            lines.append(actions.choice(self.rng, actions.WISH_THANKS))
        return self._commit(user, "wish", deltas, lines, w.exp, title="许愿池", intimacy_gain=0)

    # -------------------------------------------------- 互动小游戏
    def _personality(self) -> Optional[str]:
        totals = {key: sum(stats.get(key, 0) for stats in self.state.round_stats.values())
                  for key in ("feed", "pet", "clean", "adventure", "play")}
        if sum(totals.values()) < 5:
            return None
        totals["adventure"] += totals.pop("play")
        key = max(totals, key=totals.get)
        return {"feed": "贪吃", "pet": "黏人", "clean": "爱干净", "adventure": "好奇"}[key]

    def _do_personality(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        self._touch()
        personality = self._personality()
        lines = [f"这轮它是「{personality}」" if personality else
                 "它还在认识大家。再多一些有效照料，性格会根据本轮习惯自然显现。",
                 "性格由群体有效照料次数最多的类型决定，只影响描述和奇遇文案，不改变数值。"]
        return ActionResult(title="肥鱼性格", lines=lines,
                            buttons=[Button("运动会", "养鱼 运动会"), Button("养鱼", "养鱼")])

    def _do_sports(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        self._touch()
        gate = self._ended_gate()
        if gate is not None:
            return gate
        user = self._user(uid, name)
        gate = self._nick_gate(user)
        if gate is not None:
            return gate
        today = datetime.fromtimestamp(self.clock())
        iso = today.isocalendar()
        week = f"{iso.year}-W{iso.week:02}"
        day = today.date().isoformat()
        game = self.state.sports_week
        if not game or game.get("week") != week:
            game = {"week": week, "entries": {}, "cheers": {}}
            self.state.sports_week = game
        choices = {"冲刺": ("energy", "爆发", "活力"),
                   "绕水草": ("clean", "灵巧", "洁净"),
                   "顶泡泡": ("mood", "稳健", "心情")}
        raw = (arg or "").strip().split("@")
        pick = raw[0]
        if len(raw) > 1 and raw[-1] != week:
            return ActionResult(ok=False, title="本周赛事已更新", lines=["请重新打开运动会，使用本周按钮。"],
                                buttons=[Button("运动会", "养鱼 运动会")])
        notice = []
        if pick == "助威":
            if game["cheers"].get(uid) == day:
                return ActionResult(ok=False, title="今天已助威", lines=["每人每天一次，明天再来为大家加油吧。"])
            game["cheers"][uid] = day
            notice.append(f"{user.display_name}为鱼友们加油！今日助威已记录（仅作应援展示）。")
        elif pick:
            if pick not in choices:
                return ActionResult(ok=False, title="鱼塘运动会", lines=["项目：冲刺、绕水草、顶泡泡；也可发送「运动会 助威」。"])
            if uid in game["entries"]:
                return ActionResult(ok=False, title="本周已参赛", lines=["每位鱼友每周参加一次，成绩已记录。"])
            stat, matched, stat_name = choices[pick]
            if len(raw) < 3:
                return ActionResult(title=f"{pick} · 选择训练策略",
                    lines=[f"参考鱼当前{stat_name}，推荐{matched}策略。",
                           "得分 = 对应状态×0.7 + 策略匹配20分 + 随机0～10分。",
                           "本次免费，不扣属性；选定策略即参赛，本周不能重赛。"],
                    buttons=[Button(strategy, f"养鱼 运动会 {pick}@{strategy}@{week}")
                             for strategy in ("稳健", "灵巧", "爆发")])
            strategy = raw[1]
            if len(raw) != 3 or strategy not in ("稳健", "灵巧", "爆发"):
                return ActionResult(ok=False, title="策略无效", lines=["请从运动会项目按钮重新选择策略。"])
            score = round(getattr(self.state.fish, stat) * 0.7 + (20 if strategy == matched else 0)
                          + self.rng.random() * 10, 1)
            game["entries"][uid] = {"name": user.display_name, "project": pick,
                                     "strategy": strategy, "score": score}
            user.sports_badges += 1
            self._round_event(f"{user.display_name}参加鱼塘运动会：{pick}，得分{score}")
            notice.append(f"参赛完成，纪念奖章 +1；本次成绩 {score:.1f} 分！")
        entries = game["entries"]
        total = sum(entry["score"] for entry in entries.values())
        lines = notice + [f"本周鱼塘运动会 · {week}",
                 "周一按服务器自然周刷新；每人每周参赛一次，不限人数，换鱼也不重置机会。",
                 f"全塘 {len(entries)} 人 · 合计 {total:.1f} 分 · 共同目标60分{'（已达成）' if total >= 60 else ''}",
                 f"你的永久纪念奖章 {user.sports_badges} 枚 · 今日助威 {sum(d == day for d in game['cheers'].values())} 人",
                 "最近10位参赛鱼友："]
        for entry in list(entries.values())[-10:]:
            lines.append(f"{entry['name']} · {entry['project']} · {entry['strategy']} · {entry['score']:.1f}分")
        buttons = [Button(project, f"养鱼 运动会 {project}@{week}") for project in choices if uid not in entries]
        buttons.extend([Button("助威", f"养鱼 运动会 助威@{week}"),
                        Button("看性格", "养鱼 性格"), Button("养鱼", "养鱼")])
        return ActionResult(title="鱼塘运动会", lines=lines, buttons=buttons)

    def _ensure_weekly(self, uid: str) -> Dict:
        now = self.clock()
        iso = datetime.fromtimestamp(now).isocalendar()
        week = f"{iso.year}-W{iso.week:02}"
        quest = self.state.weekly_quest
        if quest and quest["week"] == week:
            return quest
        active = {k for k, user in self.state.users.items() if user.last_care_at is not None
                  and 0 <= now - user.last_care_at <= 7 * 86400}
        active.add(uid)
        people = min(3, max(1, len(active)))
        quest = {"week": week, "people": people, "done": False,
                 "goals": {"food": 3 * people, "clean": 2 * people, "pet": 3 * people},
                 "progress": {"food": 0, "clean": 0, "pet": 0}, "contributors": {}}
        self.state.weekly_quest = quest
        return quest

    def _progress_weekly(self, uid: str, key: str, deltas: Dict, lines: List[str]) -> None:
        kind, stat = {"feed": ("food", "饱腹"), "snack": ("food", "饱腹"),
                      "clean": ("clean", "洁净"), "pet": ("pet", "心情")}.get(key, (None, None))
        # Check the action itself before a completed heartwish can add mood.
        if kind is None or deltas.get(stat, 0) <= 0:
            return
        quest = self._ensure_weekly(uid)
        if quest["done"]:
            return
        needs_people = len(quest["contributors"]) < quest["people"]
        if (quest["progress"][kind] >= quest["goals"][kind]
                and not (needs_people and uid not in quest["contributors"])):
            return
        day = datetime.fromtimestamp(self.clock()).date().isoformat()
        contributor = quest["contributors"].setdefault(uid, {"count": 0, "day": day, "daily": {}})
        if contributor["day"] != day:
            contributor["day"], contributor["daily"] = day, {}
        if contributor["daily"].get(kind, 0) >= 2:
            return
        contributor["daily"][kind] = contributor["daily"].get(kind, 0) + 1
        contributor["count"] += 1
        quest["progress"][kind] = min(quest["goals"][kind], quest["progress"][kind] + 1)
        labels = {"food": "投喂/加餐", "clean": "净水", "pet": "逗鱼"}
        lines.append(f"本周委托 · {labels[kind]} {quest['progress'][kind]}/{quest['goals'][kind]}")
        if (len(quest["contributors"]) < quest["people"] or
                any(quest["progress"][k] < goal for k, goal in quest["goals"].items())):
            return
        quest["done"] = True
        people = []
        for member, contribution in quest["contributors"].items():
            user = self.state.users[member]
            user.materials += 2
            user.keepsakes["周委托纪念章"] = user.keepsakes.get("周委托纪念章", 0) + 1
            people.append({"uid": member, "name": user.display_name, "count": contribution["count"]})
        report = {"week": quest["week"], "completed_at": self.clock(),
                  "goals": dict(quest["goals"]), "people": people}
        self.state.weekly_reports.append(report)
        self.state.weekly_reports[:] = self.state.weekly_reports[-8:]
        self._round_event(f"鱼友们完成了{quest['week']}周委托")
        lines.append("本周委托完成！所有贡献者各获建设材料 +2、周委托纪念章 +1，已自动到账。")

    def _do_weekly(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        self._touch()
        user = self._user(uid, name)
        gate = self._nick_gate(user)
        if gate is not None:
            return gate
        quest = self._ensure_weekly(uid)
        lines = [f"{quest['week']} · {'已完成，奖励已到账' if quest['done'] else '进行中'}",
                 f"本周档位：{quest['people']}人 · 已有{len(quest['contributors'])}位贡献者",
                 "有效投喂/加餐、净水、逗鱼自动计入，每人每类每天最多2次；目标满后不再累计，但人数不足时新鱼友仍可补位。"]
        for key, label in (("food", "投喂/加餐"), ("clean", "净水"), ("pet", "逗鱼")):
            lines.append(f"{label}：{quest['progress'][key]}/{quest['goals'][key]}")
        lines.extend(["档位按生成时近7天实际照料人数（含当前鱼友）定为1～3人，整周固定。",
                      "全部目标达成且贡献者达到档位人数时，每位贡献者自动获材料2份和纪念章1枚。",
                      "周一按服务器本地自然周刷新；换鱼保留进度，未完成目标不顺延。"])
        return ActionResult(title="每周鱼塘委托", lines=lines, buttons=[
            Button("投喂", "养鱼 投喂"), Button("净水", "养鱼 净水"), Button("逗鱼", "养鱼 逗鱼"),
            Button("历期周报", "养鱼 周报"), Button("建设", "养鱼 建设"), Button("养鱼", "养鱼")])

    def _do_weekly_reports(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        self._touch()
        lines = []
        for report in reversed(self.state.weekly_reports):
            people = report["people"]
            lines.append(f"{report['week']}委托完成 · {len(people)}位贡献者 · 共{sum(p['count'] for p in people)}次")
            lines.append("共同完成：" + "、".join(f"{p['name']}({p['count']})" for p in people[:8]))
            if len(people) > 8:
                lines.append(f"另有{len(people) - 8}位鱼友，完整署名已保存。")
        return ActionResult(title="鱼塘周报 · 最近八期", lines=lines or ["还没有完成的周委托，平时照料就能一起推进。"],
                            buttons=[Button("本周委托", "养鱼 委托"), Button("纪念物", "养鱼 纪念物"), Button("养鱼", "养鱼")])

    def _do_games(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        self._touch()
        gate = self._ended_gate()
        if gate is not None:
            return gate
        g, t = self.cfg.guess, self.cfg.treasure
        lines = [
            f"猜鱼心思｜猜1～10，{g.attempts}次机会，冷却{g.cooldown_min}分钟。",
            f"合力寻宝｜三人接力探路、搬石、开箱，{t.expire_min}分钟内完成。",
            "大肥鱼游历｜签到获得机会，完成3～5段路线带回白饭和Token。",
            "Token陪玩｜消耗1枚Token，猜中后获得额外成长。",
            "鱼塘奇遇｜每天每人一次，选择路线，带回故事与纪念物。",
            "今日心愿｜正常照料就能共同完成，每天一个小目标。",
            "纪念物｜查看个人收藏，换鱼后继续保留。",
            "共建鱼塘｜捐材料建装饰，让大家的鱼塘慢慢变样。",
            "纪念册｜收藏每一轮合影，回看鱼友们的陪伴。",
            "运动会｜每周选项目和策略参赛，也能每日助威。",
            "肥鱼性格｜照料习惯养出不同性格，仅影响描述。",
            "每周委托｜平时照料累计进度，完成获得材料和纪念章。",
            "历期周报｜回顾最近八期完成记录与贡献者署名。",
        ]
        if self.state.pending_treasure:
            lines.append(f"寻宝接力进行中：已完成 {len(self.state.pending_treasure['got'])}/3 步，快来搭把手！")
        return ActionResult(title="鱼塘小游戏", lines=lines, buttons=[
            Button("🐟 猜鱼心思", "养鱼 猜鱼"), Button("🗺 合力寻宝", "养鱼 寻宝"),
            Button("🧭 大肥鱼游历", "养鱼 游历"), Button("Token陪玩", "养鱼 Token陪玩"),
            Button("🌊 鱼塘奇遇", "养鱼 奇遇"), Button("💭 今日心愿", "养鱼 心愿"),
            Button("🎁 纪念物", "养鱼 纪念物"),
            Button("共建鱼塘", "养鱼 建设"), Button("纪念册", "养鱼 纪念册"),
            Button("运动会", "养鱼 运动会"), Button("性格", "养鱼 性格"),
            Button("每周委托", "养鱼 委托"), Button("历期周报", "养鱼 周报"),
            Button("养鱼", "养鱼")])

    def _ensure_heartwish(self, uid: str) -> Dict:
        now = self.clock()
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        wish = self.state.daily_heartwish
        if wish and wish["day"] == day:
            return wish
        cfg, fish = self.cfg.heartwish, self.state.fish
        active = {key for key, u in self.state.users.items() if u.last_care_at is not None
                  and 0 <= now - u.last_care_at <= cfg.active_days * 86400}
        active.add(uid)
        goal = max(1, min(cfg.max_people, len(active)))
        if fish.clean < 50:
            kind, title, goal = "clean", "想在清清的水里打个滚：请一位鱼友净水", 1
        elif fish.satiety < 55:
            kind, title, goal = "food", "肚子有点空：请一位鱼友投喂或加餐", 1
        else:
            kind, title = "pet", f"想被 {goal} 位不同鱼友摸摸头：一起逗鱼吧"
        wish = {"day": day, "kind": kind, "title": title, "goal": goal,
                "got": [], "done": False}
        self.state.daily_heartwish = wish
        return wish

    def _progress_heartwish(self, user: UserInfo, key: str, deltas: Dict, lines: List[str]) -> None:
        wish = self.state.daily_heartwish
        if not wish or wish["done"]:
            return
        eligible = {"pet": ("pet", "心情"), "clean": ("clean", "洁净"),
                    "feed": ("food", "饱腹"), "snack": ("food", "饱腹")}
        kind, stat = eligible.get(key, (None, None))
        if kind != wish["kind"] or deltas.get(stat, 0) <= 0:
            return
        uid = next(k for k, u in self.state.users.items() if u is user)
        if uid in wish["got"]:
            return
        wish["got"].append(uid)
        lines.append(f"💭 今日心愿进度：{len(wish['got'])}/{wish['goal']} 位鱼友")
        if len(wish["got"]) >= wish["goal"]:
            wish["done"] = True
            for member in wish["got"]:
                self.state.users[member].materials += 1
            lines.append("参与心愿的鱼友各获建设材料 +1，可在「建设」捐给公共装饰。")
            self._round_event("大家完成了心愿：" + wish["title"])
            before = self.state.fish.mood
            self.state.fish.mood = actions.clamp(before + self.cfg.heartwish.mood_gain)
            deltas["心情"] = deltas.get("心情", 0) + self.state.fish.mood - before
            lines.append("心愿完成！它开心地绕着大家游了一圈，收下了今天的陪伴。")

    def _do_heartwish(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        user, gate = self._care_gate(uid, name)
        if gate is not None:
            return gate
        wish = self.state.daily_heartwish
        command = {"pet": "逗鱼", "clean": "净水", "food": "投喂"}[wish["kind"]]
        return ActionResult(title="今日小心愿", lines=[wish["title"],
            f"{'已完成' if wish['done'] else '进行中'} · {len(wish['got'])}/{wish['goal']} 位鱼友",
            "完成后参与者各获1份建设材料，用于共建永久装饰。",
            f"不同鱼友各计一次，有效照料自动推进；完成后鱼心情 +{self.cfg.heartwish.mood_gain:g}（上限100）。",
            "按服务器自然日刷新，今日目标固定；换鱼不会重领奖励。"],
            buttons=[Button(command, f"养鱼 {command}"), Button("小游戏", "养鱼 小游戏"), Button("养鱼", "养鱼")])

    def _travel_card(self, game: Dict, lines: Optional[List[str]] = None) -> ActionResult:
        scene, left, right = travel.SCENES[game["step"] % len(travel.SCENES)]
        step = game["step"] + 1
        text = lines or [f"第 {step}/{game['steps']} 段：来到{scene}，选一条路继续。"]
        text.append("旅程中的选择会带回白饭或 Token，也可能遇见朋友。")
        buttons = [
            Button(left, f"养鱼 游历 1@{game['token']}"),
            Button(right, f"养鱼 游历 2@{game['token']}"),
        ]
        return ActionResult(event="game_open", title="大肥鱼游历", lines=text, buttons=buttons,
                            data={"step": step, "steps": game["steps"]})

    def _do_travel(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        user, gate = self._care_gate(uid, name)
        if gate is not None:
            return gate
        game = self.state.pending_travel
        if not game:
            if arg:
                return ActionResult(ok=False, title="游历已结束", lines=["发「游历」开始新的旅程。"],
                                    buttons=self._main_buttons())
            if user.travel_tickets < 1:
                return ActionResult(ok=False, title="还没有游历机会",
                                    lines=["签到可获得游历机会，完成旅程后才能带回道具。"],
                                    buttons=[Button("签到", "养鱼 签到"), *self._main_buttons()])
            user.travel_tickets -= 1
            game = {"uid": uid, "name": user.display_name,
                    "steps": self.rng.randint(3, 5), "step": 0,
                    "earned_rice": 0, "earned_tokens": 0,
                    "token": str(int(self.clock() * 1000)),
                    "expires": self.clock() + 1800}
            self.state.pending_travel = game
            if not user.liangdi_seen and self.rng.random() < 0.0005:
                user.liangdi_seen = True
                user.rice += 99
                user.tokens += 99
                self.state.pending_travel = None
                return ActionResult(title="隐藏游历：梁帝", lines=[
                    "梁帝突然现身，挥手赏下满满一袋道具！",
                    "白饭 +99 · Token +99（此隐藏事件每位鱼友仅能遇见一次）",
                ], deltas={"白饭": 99, "Token": 99}, buttons=self._main_buttons())
            return self._travel_card(game, [
                f"{game['name']}带着大肥鱼出发了，这趟要经历 {game['steps']} 段旅程。"])

        if game["uid"] != uid:
            return ActionResult(ok=False, title="这是别人的旅程",
                                lines=[f"由 {game['name']} 带队，等他完成这趟游历。"],
                                buttons=self._main_buttons())
        pick, _, token = (arg or "").partition("@")
        if token and token != game["token"]:
            return ActionResult(ok=False, title="这趟旅程已更新", lines=["发「游历」查看当前进度。"],
                                buttons=self._main_buttons())
        if not pick:
            return self._travel_card(game)
        if pick not in ("1", "2"):
            result = self._travel_card(game, ["请选择按钮上的路线继续。"])
            result.ok = False
            return result

        rice_gain = 1 if pick == "1" else 0
        token_gain = 1 if pick == "2" else 0
        user.rice += rice_gain
        user.tokens += token_gain
        game["earned_rice"] += rice_gain
        game["earned_tokens"] += token_gain
        lines = ["大肥鱼顺利走完这一段：" + ("白饭 +1" if pick == "1" else "Token +1")]
        if self.rng.random() < 0.20:
            friend = actions.choice(self.rng, list(travel.FRIENDS))
            flavor, rice, tokens = travel.FRIENDS[friend]
            user.rice += rice
            user.tokens += tokens
            game["earned_rice"] += rice
            game["earned_tokens"] += tokens
            lines.append(f"遇见老朋友 {friend}！{flavor}")
            keepsake = f"{friend}的旅行纪念"
            user.keepsakes[keepsake] = user.keepsakes.get(keepsake, 0) + 1
            lines.append(f"收藏纪念物「{keepsake}」+1")
            if rice:
                lines.append(f"白饭 +{rice}")
            if tokens:
                lines.append(f"Token +{tokens}")

        game["step"] += 1
        if game["step"] >= game["steps"]:
            self.state.pending_travel = None
            lines.append(f"本趟收获：白饭 +{game['earned_rice']} · Token +{game['earned_tokens']}。")
            return ActionResult(title="平安返塘", lines=lines, buttons=self._main_buttons(),
                                deltas={"白饭": 1 if pick == "1" else 0,
                                        "Token": 1 if pick == "2" else 0})
        return self._travel_card(game, lines)

    def _adventure_card(self, game: Dict, lines: Optional[List[str]] = None) -> ActionResult:
        cfg = self.cfg.adventure
        title, intro, _, item = STORIES[game["story"]]
        personality = self._personality()
        if personality:
            intro = f"{intro}「{personality}」的它格外认真。"
        if game["node"] == "start":
            choices = ("帮忙", "探索", "绕行")
            hints = [f"帮忙：活力 -{cfg.help_energy:g}，心情 +{cfg.mood_gain:g}，获得「{item}」。",
                     f"探索：活力 -{cfg.explore_energy:g}，查看线索后可选择深入或返回。",
                     "绕行：结束本次奇遇，无额外收益或消耗。"]
        else:
            choices = ("深入", "返回")
            hints = ["细沙里有旧贝壳，也有滑溜溜的青苔。",
                     f"深入：再耗 {cfg.deep_energy:g} 活力，{cfg.success_chance:.0%} 概率找到「{item}」并增加心情 {cfg.mood_gain:g}；",
                     f"否则心情 -{cfg.fail_mood_loss:g}。返回：安全结束，不再消耗活力。"]
        return ActionResult(event="game_open", title=f"鱼塘奇遇 · {title}", lines=(lines or [intro]) + hints + [
            f"由 {game['name']} 选择，{cfg.expire_min} 分钟内完成；超时不返还已消耗活力。",
            "每人每天一次，开场即占用次数；绕行、返回或超时也会结束今日机会。"],
            buttons=[Button(choice, f"养鱼 奇遇 {choice}@{game['token']}") for choice in choices]
                    + [Button("养鱼", "养鱼")])

    def _do_adventure(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        user, gate = self._care_gate(uid, name)
        if gate is not None:
            return gate
        cfg, fish = self.cfg.adventure, self.state.fish
        game = self.state.pending_adventure
        pick, _, token = (arg or "").strip().partition("@")
        if not game:
            if pick:
                return ActionResult(ok=False, title="奇遇已结束", lines=["先发「奇遇」查看今日机会。"], buttons=self._main_buttons())
            day = time.strftime("%Y-%m-%d", time.localtime(self.clock()))
            if user.last_adventure_day == day:
                return ActionResult(ok=False, title="今日已经出游", lines=["每天每人一次，明天再来；「纪念物」可查看收藏。"], buttons=self._main_buttons())
            if fish.energy < min(cfg.help_energy, cfg.explore_energy):
                return ActionResult(ok=False, title="先休息一下", lines=["活力不足以帮忙或探索，休息后再出游，不扣今日次数。"], buttons=self._main_buttons())
            pool = [key for key in STORIES if key != "crab_return" or "crab_helped" in self.state.story_flags]
            story = actions.choice(self.rng, pool)
            game = {"story": story, "uid": uid, "name": user.display_name, "node": "start",
                    "token": str(int(self.clock() * 1000)), "expires": self.clock() + cfg.expire_min * 60}
            self.state.pending_adventure = game
            user.last_adventure_day = day
        if not pick:
            return self._adventure_card(game)
        if game["uid"] != uid or (token and token != game["token"]):
            return ActionResult(ok=False, title="请让发起者选择", lines=["这条路线只由当前发起者决定，旧奇遇按钮也不能操作新故事。"], buttons=[Button("查看奇遇", "养鱼 奇遇")])
        valid = ("帮忙", "探索", "绕行") if game["node"] == "start" else ("深入", "返回")
        if pick not in valid:
            result = self._adventure_card(game, ["请选择当前路线：" + " / ".join(valid)])
            result.ok = False
            return result
        cost = {"帮忙": cfg.help_energy, "探索": cfg.explore_energy, "深入": cfg.deep_energy}.get(pick, 0)
        if fish.energy < cost:
            result = self._adventure_card(game, [f"当前活力 {fish.energy:.0f}，这条路线需要 {cost:g}；可等恢复或选安全离开。"])
            result.ok = False
            return result
        fish.energy -= cost
        if pick == "探索":
            game["node"] = "deep"
            result = self._adventure_card(game, ["它沿着线索游到石缝前，发现里面藏着一个旧盒子。"])
            result.deltas = {"活力": -cost}
            return result
        self.state.pending_adventure = None
        if pick in ("绕行", "返回"):
            return ActionResult(title="平安归来", lines=["它记住了这处风景，甩甩尾巴游回鱼塘。今日奇遇结束。"], buttons=self._main_buttons())
        won = pick == "帮忙" or self.rng.random() < cfg.success_chance
        _, _, help_text, item = STORIES[game["story"]]
        before = fish.mood
        fish.mood = actions.clamp(before + (cfg.mood_gain if won else -cfg.fail_mood_loss))
        lines = [help_text if pick == "帮忙" else ("旧盒子里藏着一件闪亮的小礼物！" if won else "脚下一滑，盒子溜进石缝。它有点失落，空手游了回来。")]
        if won:
            user.materials += 1
            self._round_event(f"{user.display_name}在奇遇中带回了{item}")
            lines.append("建设材料 +1，可在「建设」捐给公共装饰。")
            user.keepsakes[item] = user.keepsakes.get(item, 0) + 1
            lines.append(f"🎁 纪念物「{item}」+1，收藏跨鱼保留。")
        if game["story"] == "crab" and pick == "帮忙" and "crab_helped" not in self.state.story_flags:
            self.state.story_flags.append("crab_helped")
            lines.append("小蟹记住了你们的鱼塘，以后也许会来串门。")
        return self._commit(user, "adventure", {"活力": -cost, "心情": fish.mood - before}, lines, cfg.exp, title="奇遇归来")

    def _do_keepsakes(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        self._touch()
        user = self._user(uid, name)
        gate = self._nick_gate(user)
        if gate is not None:
            return gate
        lines = [f"{item} ×{count}" for item, count in user.keepsakes.items()]
        return ActionResult(title=f"{user.display_name}的纪念物", lines=lines or ["还没有收藏。发「奇遇」去发现小故事吧！"],
                            buttons=[Button("奇遇", "养鱼 奇遇"), Button("养鱼", "养鱼")])

    def _guess_buttons(self, game: Dict) -> List[Button]:
        return [Button(str(n), f"养鱼 猜鱼 {n}")
                for n in range(game["low"], game["high"] + 1)] + [Button("养鱼", "养鱼")]

    def _do_guess(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        user, gate = self._care_gate(uid, name)
        if gate is not None:
            return gate
        cfg, fish = self.cfg.guess, self.state.fish
        game = self.state.pending_guess.get(uid)
        pick = (arg or "").strip()
        if not pick:
            if game:
                return ActionResult(title="猜鱼心思", lines=[
                    f"它心里的数字在 {game['low']}～{game['high']}，还剩 {game['left']} 次机会。"],
                    buttons=self._guess_buttons(game))
            if user.last_guess_at is not None:
                remain = cfg.cooldown_min * 60 - (self.clock() - user.last_guess_at)
                if remain > 0:
                    return self._cd_busy("猜鱼", remain)
            if fish.energy < cfg.energy_cost:
                return ActionResult(ok=False, title="游不动了", lines=[
                    f"猜鱼需要 {cfg.energy_cost:g} 活力，先投喂或等它休息吧。"], buttons=self._main_buttons())
            fish.energy -= cfg.energy_cost
            user.last_guess_at = self.clock()
            game = {"hidden": self.rng.randrange(10) + 1, "low": 1, "high": 10,
                    "left": cfg.attempts, "expires": self.clock() + cfg.expire_min * 60}
            self.state.pending_guess[uid] = game
            return ActionResult(event="game_open", title="猜鱼心思", lines=[
                "它偷偷想了一个 1～10 的数字！",
                f"机会：{cfg.attempts}次 · 时限：{cfg.expire_min}分钟",
                "操作：点数字按钮，或发送「猜鱼 数字」。",
                "提示：猜错会告诉你大了还是小了。",
                f"猜中：白饭 +{cfg.reward_snacks}、心情 +{cfg.mood_win:g}",
                f"机会用完：心情 +{cfg.mood_miss:g}"],
                deltas={"活力": -cfg.energy_cost}, buttons=self._guess_buttons(game))
        if not game:
            return ActionResult(ok=False, title="没有开局", lines=["先发「猜鱼」开局；超时的游戏已结束。"],
                                buttons=self._main_buttons())
        if pick not in {str(n) for n in range(game["low"], game["high"] + 1)}:
            return ActionResult(ok=False, title="换个数字", lines=[
                f"请输入 {game['low']}～{game['high']} 的整数，这次不扣机会。"], buttons=self._guess_buttons(game))
        n = int(pick)
        game["left"] -= 1
        won = n == game["hidden"]
        if not won and game["left"] > 0:
            if n < game["hidden"]:
                game["low"] = n + 1
            else:
                game["high"] = n - 1
            return ActionResult(title="猜鱼心思", lines=[
                f"{'小了' if n < game['hidden'] else '大了'}！还剩 {game['left']} 次，范围 {game['low']}～{game['high']}。"],
                buttons=self._guess_buttons(game))
        del self.state.pending_guess[uid]
        before = fish.mood
        fish.mood = actions.clamp(fish.mood + (cfg.mood_win if won else cfg.mood_miss))
        deltas = {"心情": fish.mood - before}
        if won:
            user.rice += cfg.reward_snacks
            deltas["白饭"] = cfg.reward_snacks
        lines = [f"猜中了！它惊讶得吐了个爱心泡泡。答案就是 {game['hidden']}。" if won else
                 f"机会用完啦！它悄悄告诉你：答案是 {game['hidden']}。蹭蹭手指，下次再猜～"]
        return self._commit(user, "guess", deltas, lines, cfg.exp, title="猜鱼结算")

    def _do_treasure(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        user, gate = self._care_gate(uid, name)
        if gate is not None:
            return gate
        cfg, fish = self.cfg.treasure, self.state.fish
        game = self.state.pending_treasure
        step = (arg or "").strip()
        roles = ("探路", "搬石", "开箱")
        if not game:
            if step:
                return ActionResult(ok=False, title="没有寻宝队伍", lines=["先发「寻宝」查看并开启接力。"],
                                    buttons=self._main_buttons())
            remain = self._cooldown_left("treasure", cfg.cooldown_min)
            if remain:
                return self._cd_busy("寻宝", remain)
            game = {"got": {}, "expires": self.clock() + cfg.expire_min * 60}
            self.state.pending_treasure = game
            fish.cooldowns["treasure"] = self.clock()
        expected = roles[len(game["got"])]
        buttons = [Button(f"接力：{expected}", f"养鱼 寻宝 {expected}"), Button("养鱼", "养鱼")]
        if not step:
            return ActionResult(title="合力寻宝", lines=[
                f"池底发现神秘宝箱！{cfg.expire_min} 分钟内由三位不同鱼友依次探路 → 搬石 → 开箱。",
                f"成功后每人白饭 +{cfg.reward_snacks}，鱼心情 +{cfg.reward_mood:g}；超时无奖励、不扣道具。",
                f"进度 {len(game['got'])}/3，下一步发「寻宝 {expected}」。"], buttons=buttons)
        if uid in game["got"] or step != expected:
            return ActionResult(ok=False, title="接力等你", lines=[
                f"下一步是「寻宝 {expected}」，需要尚未参与的鱼友来完成；每人只能接力一次。"], buttons=buttons)
        game["got"][uid] = user.display_name
        if len(game["got"]) < len(roles):
            next_role = roles[len(game["got"])]
            return ActionResult(title="寻宝接力", lines=[
                f"{user.display_name} 完成了{step}！进度 {len(game['got'])}/3，等另一位鱼友「寻宝 {next_role}」。"],
                buttons=[Button(f"接力：{next_role}", f"养鱼 寻宝 {next_role}"), Button("养鱼", "养鱼")])
        self.state.pending_treasure = None
        for participant in game["got"]:
            keeper = self.state.users[participant]
            keeper.rice += cfg.reward_snacks
            if participant != uid:
                keeper.intimacy += 1
                keeper.total_interactions += 1
                self.state.community_exp += 1
        before = fish.mood
        fish.mood = actions.clamp(fish.mood + cfg.reward_mood)
        return self._commit(user, "treasure_finish", {"心情": fish.mood - before, "白饭": cfg.reward_snacks},
                            ["宝箱打开，白饭哗啦啦飘了出来！",
                             f"感谢 {'、'.join(game['got'].values())}：每人白饭 +{cfg.reward_snacks}、亲密 +1。"],
                            cfg.exp, title="寻宝成功")

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

    def _round_event(self, text: str) -> None:
        self.state.round_events.append(text)
        self.state.round_events[:] = self.state.round_events[-6:]

    def _do_build(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        self._touch()
        user = self._user(uid, name)
        gate = self._nick_gate(user)
        if gate is not None:
            return gate
        lines = []
        target = (arg or "").strip()
        if target:
            if target not in DECORATIONS:
                return ActionResult(ok=False, title="建设", lines=["请选择水草角、贝壳小屋或月亮灯。"])
            donors = self.state.decorations.setdefault(target, {})
            if sum(donors.values()) >= DECORATIONS[target]:
                return ActionResult(ok=False, title="已经建好啦", lines=["材料未扣除，换个项目继续建设吧。"],
                                    buttons=[Button("建设", "养鱼 建设")])
            if user.materials < 1:
                return ActionResult(ok=False, title="还缺材料", lines=["完成今日心愿、周委托或成功奇遇可获材料，每次捐1份。"],
                                    buttons=[Button("心愿", "养鱼 心愿"), Button("奇遇", "养鱼 奇遇")])
            user.materials -= 1
            donors[uid] = donors.get(uid, 0) + 1
            lines.append(f"{user.display_name}为{target}捐出1份材料，谢谢搭把手！")
            if sum(donors.values()) == DECORATIONS[target]:
                lines.append(f"{target}建成！已加入鱼塘场景，换鱼后继续保留。")
                if self.state.status == "alive":
                    self._round_event(f"鱼友们一起建成了{target}")
        lines.extend([f"你的建设材料：{user.materials}份。每次点击捐1份材料。",
                      "心愿完成时参与者、成功奇遇各得1份材料；周委托贡献者各得2份。装饰与进度跨鱼保留，仅改变外观。"])
        buttons = []
        for decor, cost in DECORATIONS.items():
            donors = self.state.decorations.get(decor, {})
            total = sum(donors.values())
            lines.append(f"{decor}：{'已建成' if total >= cost else f'{total}/{cost}'}")
            if donors:
                top = sorted(donors.items(), key=lambda kv: -kv[1])[:3]
                lines.append("  共建鱼友：" + "、".join(f"{self.state.users[k].display_name}({v})" for k, v in top))
            if total < cost:
                buttons.append(Button(f"捐给{decor}·1份", f"养鱼 建设 {decor}"))
        buttons.extend([Button("纪念册", "养鱼 纪念册"), Button("养鱼", "养鱼")])
        return ActionResult(title="共建鱼塘", lines=lines, buttons=buttons)

    def _memorial_snapshot(self) -> Dict:
        # Freeze both names and scenery before the next fish or nickname changes them.
        return deepcopy({"fish": self.state.fish.to_dict(),
                         "decorations": self.state.decorations,
                         "personality": self._personality(),
                         "participants": [{"uid": uid, "name": self.state.users[uid].display_name, **stats}
                                          for uid, stats in self.state.round_stats.items()],
                         "events": self.state.round_events})

    def _do_memorial(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        self._touch()
        total = len(self.state.archive)
        if not total:
            return ActionResult(title="鱼塘纪念册", lines=["还没有合影。毕业或离别时会自动保存这一轮的故事。"],
                                buttons=[Button("养鱼", "养鱼")])
        try:
            number = int(arg) if arg else total
        except ValueError:
            number = 0
        if not 1 <= number <= total:
            return ActionResult(ok=False, title="选择纪念页", lines=[f"请输入「养鱼 纪念册 序号」，范围1～{total}。"])
        page = deepcopy(self.state.archive[number - 1])
        lines = memorial_lines(page)
        buttons = []
        if number > 1:
            buttons.append(Button("上一页", f"养鱼 纪念册 {number - 1}"))
        if number < total:
            buttons.append(Button("下一页", f"养鱼 纪念册 {number + 1}"))
        buttons.extend([Button("档案", "养鱼 档案"), Button("养鱼", "养鱼")])
        return ActionResult(title=f"鱼塘纪念册 · {number}/{total}", lines=lines,
                            data={"memorial": page}, buttons=buttons)

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
        personality = self._personality()
        if personality:
            lines.insert(0, f"本轮性格：{personality}")
        r = ActionResult(title="出栏图鉴", lines=lines)
        r.buttons = [Button("最新合影", "养鱼 纪念册"), Button("建设", "养鱼 建设"), *self._main_buttons()[:9]]
        return r

    # -------------------------------------------------- 结束与重开
    def _do_graduate(self, uid: str, name: str, arg: Optional[str]) -> ActionResult:
        self._touch()
        if self.state.status == "ended":
            return self._settlement_result()
        user = self._user(uid, name)
        gate = self._nick_gate(user)
        if gate is not None:
            return gate
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
        user = self._user(uid, name)
        gate = self._nick_gate(user)
        if gate is not None:
            return gate
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
        self.state.pending_guess.clear()
        self.state.pending_treasure = None
        self.state.pending_rescue = None
        self.state.pending_adventure = None
        self.state.pending_travel = None
        self.state.round_stats.clear()
        self.state.round_events.clear()
        for u in self.state.users.values():
            u.intimacy = 0     # 亲密跟着鱼走；钱包和累计次数保留
            u.cooldowns.clear()
        r = ActionResult(event="restart", title="新的一生",
                         lines=[actions.choice(self.rng, actions.NEW_FISH),
                                f"这一尾叫「{fish.name}」。档案、白饭、Token、海苔、材料和装饰都还在，接着加油！"])
        r.buttons = self._main_buttons()
        return r

    def _do_help(self, uid: Optional[str] = None, name: Optional[str] = None,
                 arg: Optional[str] = None) -> ActionResult:
        r = ActionResult(title="帮助", lines=list(HELP_LINES))
        r.buttons = self._main_buttons()
        return r

    # -------------------------------------------------- 结算
    def _end(self, reason: str, now: float, force_grade: Optional[str] = None) -> None:
        if self.state.status == "ended":
            return
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
                first.rice += base
                rewards.append((first.display_name or "匿名鱼友", base))
                half = base // 2
                for u in ranked[1:3]:
                    if half > 0:
                        u.rice += half
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
        self.state.pending_adventure = None
        self.state.pending_travel = None
        self.state.pending_play = None
        self.state.pending_guess.clear()
        self.state.pending_treasure = None
        self.state.archive.append({
            "fish_name": fish.name, "reason": reason, "grade": grade_val, "score": score_val,
            "weight": round(fish.weight, 1), "days": days, "ended_at": now,
            "memorial": self._memorial_snapshot(),
        })
        self.state.archive[:] = self.state.archive[-ARCHIVE_LIMIT:]

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
                lines.append(f"感谢 {name} 的悉心照料：白饭 +{amount}")
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
        if self.state.archive and self.state.archive[-1].get("memorial"):
            r.data["memorial"] = deepcopy(self.state.archive[-1])
        r.buttons = self._end_buttons()
        return r


def memorial_lines(page: Dict) -> List[str]:
    lines = [f"{page.get('fish_name', '?')} · {'毕业合影' if page.get('reason') == 'graduated' else '离别留念'}",
             f"{page.get('grade', 'C')}级 · 体重{page.get('weight', 0)} · 相伴{page.get('days', 0)}天"]
    snapshot = page.get("memorial")
    if not isinstance(snapshot, dict):
        return lines + ["旧版档案：当时未记录合影、参与者与故事。"]
    if snapshot.get("personality"):
        lines.append("本轮性格：" + snapshot["personality"])
    raw_people = snapshot.get("participants")
    people = [person for person in raw_people if isinstance(person, dict)] \
        if isinstance(raw_people, list) else []
    lines.append(f"本轮参与照料：{len(people)}位鱼友")
    if people:
        lines.append("首次照料：" + people[0].get("name", "匿名鱼友"))
        for key, title in (("care", "照料担当"), ("clean", "净水担当"), ("adventure", "奇遇发现者")):
            leader = max(people, key=lambda p: p.get(key, 0))
            if leader.get(key, 0):
                lines.append(f"{title}：{leader.get('name', '匿名鱼友')}（{leader[key]}次）")
        lines.append("合影签名：" + "、".join(p.get("name", "匿名鱼友") for p in people[:12]))
        if len(people) > 12:
            lines.append(f"还有{len(people) - 12}位鱼友，完整名单已保存在档案中。")
    else:
        lines.append("本轮暂无参与记录；旧存档升级前的照料未补记。")
    decorations = snapshot.get("decorations")
    built = completed(decorations) if isinstance(decorations, dict) else []
    lines.append("合影装饰：" + ("、".join(built) or "自然鱼塘"))
    events = snapshot.get("events")
    lines.extend(events if isinstance(events, list) and events
                 else ["平凡的陪伴，也是值得收藏的一页。"])
    return lines
