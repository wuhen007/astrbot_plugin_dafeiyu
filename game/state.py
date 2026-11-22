"""鱼塘状态与序列化：所有持久化数据都在这里定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

ARCHIVE_LIMIT = 20


@dataclass
class FishState:
    """一条鱼。四项属性一律 0~100（体重 0~max_weight）。"""

    name: str = ""
    exp: int = 0
    weight: float = 0.0  # 体重（养成度）
    satiety: float = 0.0  # 饱腹
    mood: float = 0.0  # 心情
    clean: float = 0.0  # 洁净
    energy: float = 0.0  # 活力
    born_at: float = 0.0
    last_tick: float = 0.0
    hunger_zero_since: Optional[float] = None  # 饱腹归零起始时刻（离家出走判定）
    mood_zero_since: Optional[float] = None
    last_action: Optional[str] = None  # 连续同一动作统计（收益递减）
    repeat_count: int = 0
    cooldowns: Dict[str, float] = field(default_factory=dict)  # 旧存档全塘冷却及全塘小游戏冷却

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "exp": self.exp,
            "weight": self.weight,
            "satiety": self.satiety,
            "mood": self.mood,
            "clean": self.clean,
            "energy": self.energy,
            "born_at": self.born_at,
            "last_tick": self.last_tick,
            "hunger_zero_since": self.hunger_zero_since,
            "mood_zero_since": self.mood_zero_since,
            "last_action": self.last_action,
            "repeat_count": self.repeat_count,
            "cooldowns": dict(self.cooldowns),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FishState":
        d = d or {}
        return cls(
            name=d.get("name", ""),
            exp=int(d.get("exp", 0)),
            weight=float(d.get("weight", 0.0)),
            satiety=float(d.get("satiety", 0.0)),
            mood=float(d.get("mood", 0.0)),
            clean=float(d.get("clean", 0.0)),
            energy=float(d.get("energy", 0.0)),
            born_at=float(d.get("born_at", 0.0)),
            last_tick=float(d.get("last_tick", 0.0)),
            hunger_zero_since=d.get("hunger_zero_since"),
            mood_zero_since=d.get("mood_zero_since"),
            last_action=d.get("last_action"),
            repeat_count=int(d.get("repeat_count", 0)),
            cooldowns={k: float(v) for k, v in (d.get("cooldowns") or {}).items()},
        )


@dataclass
class UserInfo:
    """一位鱼友在本塘的档案。"""

    display_name: str = ""
    nick_set: bool = False  # 是否已登记名号（未登记时照料动作会被引导登记）
    intimacy: int = 0  # 与当前这条鱼的亲密（换鱼重置）
    total_interactions: int = 0
    snacks: int = 0  # 绝版海苔，仅保留旧库存
    rice: int = 0
    tokens: int = 0
    travel_tickets: int = 0
    liangdi_seen: bool = False
    last_bonus_day: Optional[str] = None
    last_sign_day: Optional[str] = None  # 最后打卡日（YYYY-MM-DD）
    sign_streak: int = 0  # 连续打卡天数
    last_guess_at: Optional[float] = None  # 猜鱼开局时间，跨鱼保留冷却
    last_care_at: Optional[float] = None
    cooldowns: Dict[str, float] = field(default_factory=dict)  # 动作 -> 个人上次执行时刻
    last_adventure_day: Optional[str] = None
    keepsakes: Dict[str, int] = field(default_factory=dict)
    materials: int = 0
    sports_badges: int = 0
    last_chat_attempt_at: Optional[float] = None
    chat_reward_day: Optional[str] = None
    chat_reward_count: int = 0

    def to_dict(self) -> dict:
        return {
            "display_name": self.display_name,
            "nick_set": self.nick_set,
            "intimacy": self.intimacy,
            "total_interactions": self.total_interactions,
            "snacks": self.snacks,
            "rice": self.rice,
            "tokens": self.tokens,
            "travel_tickets": self.travel_tickets,
            "liangdi_seen": self.liangdi_seen,
            "last_bonus_day": self.last_bonus_day,
            "last_sign_day": self.last_sign_day,
            "sign_streak": self.sign_streak,
            "last_guess_at": self.last_guess_at,
            "last_care_at": self.last_care_at,
            "cooldowns": dict(self.cooldowns),
            "last_adventure_day": self.last_adventure_day,
            "keepsakes": dict(self.keepsakes),
            "materials": self.materials,
            "sports_badges": self.sports_badges,
            "last_chat_attempt_at": self.last_chat_attempt_at,
            "chat_reward_day": self.chat_reward_day,
            "chat_reward_count": self.chat_reward_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "UserInfo":
        d = d or {}
        return cls(
            display_name=d.get("display_name", ""),
            nick_set=bool(d.get("nick_set", False)),
            intimacy=int(d.get("intimacy", 0)),
            total_interactions=int(d.get("total_interactions", 0)),
            snacks=int(d.get("snacks", 0)),
            rice=int(d.get("rice", 0)),
            tokens=int(d.get("tokens", 0)),
            travel_tickets=int(d.get("travel_tickets", 0)),
            liangdi_seen=bool(d.get("liangdi_seen", False)),
            last_bonus_day=d.get("last_bonus_day"),
            last_sign_day=d.get("last_sign_day"),
            sign_streak=int(d.get("sign_streak", 0)),
            last_guess_at=d.get("last_guess_at"),
            last_care_at=d.get("last_care_at"),
            cooldowns={k: float(v) for k, v in (d.get("cooldowns") or {}).items()},
            last_adventure_day=d.get("last_adventure_day"),
            keepsakes=dict(d.get("keepsakes") or {}),
            materials=int(d.get("materials", 0)),
            sports_badges=int(d.get("sports_badges", 0)),
            last_chat_attempt_at=d.get("last_chat_attempt_at"),
            chat_reward_day=d.get("chat_reward_day"),
            chat_reward_count=int(d.get("chat_reward_count", 0)),
        )


@dataclass
class PondState:
    """一座鱼塘 = 一个会话（群/私聊）的完整存档。"""

    version: int = 1
    session_key: str = ""
    status: str = "alive"  # alive / ended
    end_reason: Optional[str] = None  # graduated / runaway
    end_at: Optional[float] = None
    end_summary: Optional[Dict] = None
    fish: FishState = field(default_factory=FishState)
    users: Dict[str, UserInfo] = field(default_factory=dict)
    community_exp: int = 0  # 全群累计互动数（鱼塘等级进度）
    pending_play: Optional[Dict] = (
        None  # 套圈进行中：{hidden, opened_by, opened_by_name, opened_at}
    )
    pending_rescue: Optional[Dict] = (
        None  # 求援进行中：{kind, text, need, got: {uid: name}, expires}
    )
    archive: List[Dict] = field(default_factory=list)  # 出栏图鉴
    pending_guess: Dict[str, Dict] = field(default_factory=dict)
    pending_treasure: Optional[Dict] = None
    pending_adventure: Optional[Dict] = None
    pending_travel: Optional[Dict] = None
    daily_heartwish: Optional[Dict] = None
    story_flags: List[str] = field(default_factory=list)
    decorations: Dict[str, Dict[str, int]] = field(default_factory=dict)
    round_stats: Dict[str, Dict[str, int]] = field(default_factory=dict)
    round_events: List[str] = field(default_factory=list)
    sports_week: Optional[Dict] = None
    weekly_quest: Optional[Dict] = None
    weekly_reports: List[Dict] = field(default_factory=list)
    daily_diary: Optional[Dict] = None

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "session_key": self.session_key,
            "status": self.status,
            "end_reason": self.end_reason,
            "end_at": self.end_at,
            "end_summary": self.end_summary,
            "fish": self.fish.to_dict(),
            "users": {k: v.to_dict() for k, v in self.users.items()},
            "community_exp": self.community_exp,
            "pending_play": self.pending_play,
            "pending_rescue": self.pending_rescue,
            "archive": list(self.archive),
            "pending_guess": dict(self.pending_guess),
            "pending_treasure": self.pending_treasure,
            "pending_adventure": self.pending_adventure,
            "pending_travel": self.pending_travel,
            "daily_heartwish": self.daily_heartwish,
            "story_flags": list(self.story_flags),
            "decorations": self.decorations,
            "round_stats": self.round_stats,
            "round_events": list(self.round_events),
            "sports_week": self.sports_week,
            "weekly_quest": self.weekly_quest,
            "weekly_reports": list(self.weekly_reports),
            "daily_diary": self.daily_diary,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PondState":
        d = d or {}
        return cls(
            version=int(d.get("version", 1)),
            session_key=d.get("session_key", ""),
            status=d.get("status", "alive"),
            end_reason=d.get("end_reason"),
            end_at=d.get("end_at"),
            end_summary=d.get("end_summary"),
            fish=FishState.from_dict(d.get("fish") or {}),
            users={k: UserInfo.from_dict(v) for k, v in (d.get("users") or {}).items()},
            community_exp=int(d.get("community_exp", 0)),
            pending_play=d.get("pending_play"),
            pending_rescue=d.get("pending_rescue"),
            archive=list(d.get("archive") or [])[-ARCHIVE_LIMIT:],
            pending_guess=dict(d.get("pending_guess") or {}),
            pending_treasure=d.get("pending_treasure"),
            pending_adventure=d.get("pending_adventure"),
            pending_travel=d.get("pending_travel"),
            daily_heartwish=d.get("daily_heartwish"),
            story_flags=list(d.get("story_flags") or []),
            decorations=dict(d.get("decorations") or {}),
            round_stats=dict(d.get("round_stats") or {}),
            round_events=list(d.get("round_events") or []),
            sports_week=d.get("sports_week"),
            weekly_quest=d.get("weekly_quest"),
            weekly_reports=list(d.get("weekly_reports") or []),
            daily_diary=d.get("daily_diary"),
        )
