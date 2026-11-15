"""鱼塘状态与序列化：所有持久化数据都在这里定义。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class FishState:
    """一条鱼。四项属性一律 0~100（体重 0~max_weight）。"""
    name: str = ""
    exp: int = 0
    weight: float = 0.0       # 体重（养成度）
    satiety: float = 0.0      # 饱腹
    mood: float = 0.0         # 心情
    clean: float = 0.0        # 洁净
    energy: float = 0.0       # 活力
    born_at: float = 0.0
    last_tick: float = 0.0
    hunger_zero_since: Optional[float] = None   # 饱腹归零起始时刻（离家出走判定）
    mood_zero_since: Optional[float] = None
    last_action: Optional[str] = None           # 连续同一动作统计（收益递减）
    repeat_count: int = 0
    cooldowns: Dict[str, float] = field(default_factory=dict)   # 动作 -> 上次执行时刻

    def to_dict(self) -> dict:
        return {
            "name": self.name, "exp": self.exp, "weight": self.weight,
            "satiety": self.satiety, "mood": self.mood, "clean": self.clean,
            "energy": self.energy, "born_at": self.born_at, "last_tick": self.last_tick,
            "hunger_zero_since": self.hunger_zero_since,
            "mood_zero_since": self.mood_zero_since,
            "last_action": self.last_action, "repeat_count": self.repeat_count,
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
    nick_set: bool = False     # 是否已登记名号（未登记时照料动作会被引导登记）
    intimacy: int = 0          # 与当前这条鱼的亲密（换鱼重置）
    total_interactions: int = 0
    snacks: int = 0            # 海苔片钱包（跨鱼保留）
    last_bonus_day: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "display_name": self.display_name, "nick_set": self.nick_set,
            "intimacy": self.intimacy,
            "total_interactions": self.total_interactions, "snacks": self.snacks,
            "last_bonus_day": self.last_bonus_day,
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
            last_bonus_day=d.get("last_bonus_day"),
        )


@dataclass
class PondState:
    """一座鱼塘 = 一个会话（群/私聊）的完整存档。"""
    version: int = 1
    session_key: str = ""
    status: str = "alive"              # alive / ended
    end_reason: Optional[str] = None   # graduated / runaway
    end_at: Optional[float] = None
    end_summary: Optional[Dict] = None
    fish: FishState = field(default_factory=FishState)
    users: Dict[str, UserInfo] = field(default_factory=dict)
    community_exp: int = 0             # 全群累计互动数（鱼塘等级进度）
    pending_play: Optional[Dict] = None   # 套圈进行中：{hidden, opened_by, opened_by_name, opened_at}
    archive: List[Dict] = field(default_factory=list)   # 出栏图鉴

    def to_dict(self) -> dict:
        return {
            "version": self.version, "session_key": self.session_key,
            "status": self.status, "end_reason": self.end_reason,
            "end_at": self.end_at, "end_summary": self.end_summary,
            "fish": self.fish.to_dict(),
            "users": {k: v.to_dict() for k, v in self.users.items()},
            "community_exp": self.community_exp,
            "pending_play": self.pending_play,
            "archive": list(self.archive),
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
            archive=list(d.get("archive") or []),
        )
