"""等级 / 阶段 / 出栏判定 / 评分 —— 纯函数。"""
from __future__ import annotations

from typing import List, Tuple

from .config import STAGE_EXP, STAGE_NAMES, GameConfig
from .state import FishState


def level_from_exp(exp: int) -> int:
    lv = 1
    for i, need in enumerate(STAGE_EXP):
        if exp >= need:
            lv = i + 1
    return min(lv, len(STAGE_NAMES))


def stage_name(level: int) -> str:
    return STAGE_NAMES[max(0, min(level, len(STAGE_NAMES))) - 1]


def exp_progress(exp: int) -> Tuple[int, ...]:
    """当前级内经验进度 -> (cur, need)；满阶时 need 为 None。"""
    lv = level_from_exp(exp)
    if lv >= len(STAGE_NAMES):
        return exp - STAGE_EXP[lv - 1], None
    base = STAGE_EXP[lv - 1]
    return exp - base, STAGE_EXP[lv] - base


def decay_multiplier(level: int, pond_level: int, cfg: GameConfig) -> float:
    """自然衰减倍率 = 阶段倍率 ×（1 - 鱼塘等级减免）。"""
    stage = cfg.stage_decay_multiplier[min(level, len(cfg.stage_decay_multiplier)) - 1]
    return stage * max(0.0, 1.0 - cfg.pond_level_decay_relief * pond_level)


def graduate_missing(fish: FishState, cfg: GameConfig) -> List[str]:
    """未满足的出栏条件（空列表 = 可以出栏）。"""
    miss = []
    lv = level_from_exp(fish.exp)
    if lv < len(STAGE_NAMES):
        miss.append(f"阶段未到（现在 {stage_name(lv)}，目标 {STAGE_NAMES[-1]}）")
    if fish.weight < cfg.graduate_weight:
        miss.append(f"体重 {fish.weight:.0f}/{cfg.graduate_weight:.0f}")
    if fish.mood < cfg.graduate_mood:
        miss.append(f"心情 {fish.mood:.0f}/{cfg.graduate_mood:.0f}")
    return miss


def score(fish: FishState, top_intimacy: float, cfg: GameConfig) -> float:
    """出栏综合评分（0~100）。"""
    return round(
        fish.weight * cfg.score_weight
        + fish.mood * cfg.score_mood
        + fish.clean * cfg.score_clean
        + min(top_intimacy, cfg.intimacy_score_cap) * cfg.score_intimacy,
        1,
    )


def grade(score_val: float, cfg: GameConfig) -> str:
    if score_val >= cfg.grade_s:
        return "S"
    if score_val >= cfg.grade_a:
        return "A"
    if score_val >= cfg.grade_b:
        return "B"
    return "C"


_REWARD_FIELD = {"S": "reward_s", "A": "reward_a", "B": "reward_b", "C": "reward_c"}


def reward_for_grade(g: str, cfg: GameConfig) -> int:
    return int(getattr(cfg, _REWARD_FIELD.get(g, "reward_c")))
