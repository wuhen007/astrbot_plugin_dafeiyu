"""《肥鱼养成记》全部可调参数。

改数值、改难度只需要动这个文件，引擎不写死任何数字。
所有数值、阶段与动作命名均为本项目原创设定。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

# 成长阶段名（Lv.1 ~ Lv.6，最后一阶即主题：大肥鱼）
STAGE_NAMES: Tuple[str, ...] = ("鱼籽", "鱼苗", "豆丁鱼", "圆滚鱼", "肥壮鱼", "大肥鱼")
# 达到各等级所需的累计经验（索引 0 对应 Lv.1）
STAGE_EXP: Tuple[int, ...] = (0, 50, 110, 200, 320, 500)

# 新鱼随机名池（原创）
FISH_NAMES: Tuple[str, ...] = ("泡泡", "团团", "阿肥", "布丁", "汤圆", "钢镚", "糯米", "胖虎")


@dataclass
class FeedAction:
    """投喂。"""
    rice_cost: int = 1
    satiety_gain: float = 22.0        # 饱腹恢复（受收益递减影响）
    energy_gain: float = 6.0          # 活力恢复
    weight_min: float = 1.5           # 增重下限
    weight_max: float = 3.5           # 增重上限
    exp: int = 8
    cooldown_min: int = 45
    overfeed_threshold: float = 92.0  # 饱腹 ≥ 此值再喂会"撑到"
    overfeed_weight_loss: float = 1.0
    overfeed_mood_loss: float = 6.0
    overfeed_exp: int = 2


@dataclass
class SnackAction:
    """加餐：消耗海苔片，无撑到风险。"""
    cost: int = 1
    satiety_gain: float = 15.0
    mood_gain: float = 8.0
    exp: int = 5
    cooldown_min: int = 15


@dataclass
class PetAction:
    """逗鱼。"""
    mood_gain: float = 6.0
    exp: int = 6
    cooldown_min: int = 15


@dataclass
class CleanAction:
    """净水。"""
    clean_gain: float = 35.0
    exp: int = 5
    cooldown_min: int = 120


@dataclass
class PlayAction:
    """玩耍：三壳套圈小游戏。"""
    exp: int = 12
    cooldown_min: int = 60
    vitality_cost: float = 20.0       # 需要的活力
    success_weight: float = 3.0       # 猜中且水干净时的增重
    success_mood: float = 8.0
    fail_mood: float = 3.0            # 猜错/捞空的安慰奖
    pending_timeout_min: int = 5      # 开局后超时作废
    token_weight_bonus: float = 3.0
    token_mood_bonus: float = 8.0


@dataclass
class WeighAction:
    """称重：看成长进度。"""
    exp: int = 2
    cooldown_min: int = 10


@dataclass
class SignConfig:
    """每日打卡。"""
    base: int = 1
    streak3: int = 2
    streak7: int = 3
    ticket_cap: int = 3


@dataclass
class FishingAction:
    """一键钓鱼：概率表按顺序累计，剩余概率为宝箱。"""
    cooldown_min: int = 30
    exp: int = 4
    p_rice: float = 0.45
    p_token: float = 0.20
    p_bundle: float = 0.25


@dataclass
class RescueConfig:
    """求援事件：照料后概率刷出，全群协作。"""
    chance: float = 0.10        # 每次照料动作触发概率
    need: int = 2               # 需要人数
    expire_min: int = 10        # 过期时间（分钟）
    reward_snacks: int = 2      # 参与者每人海苔片
    reward_mood: float = 10.0   # 完成时鱼心情
    expire_mood_loss: float = 5.0  # 无人救援过期时鱼心情


@dataclass
class WishConfig:
    """许愿池：花海苔片随机换增益。"""
    cost: int = 2               # 海苔片消耗
    exp: int = 6
    mood_gain: float = 20.0
    energy_gain: float = 40.0
    weight_gain: float = 3.0
    refund: int = 1             # 谢谢参与时返还海苔片


@dataclass
class GuessConfig:
    """猜鱼心思：每人独立开局，冷却从开局计算。"""
    cooldown_min: int = 30
    expire_min: int = 5
    attempts: int = 3
    energy_cost: float = 10.0
    reward_snacks: int = 1
    mood_win: float = 8.0
    mood_miss: float = 2.0
    exp: int = 5


@dataclass
class TreasureConfig:
    """合力寻宝：三位不同鱼友依次接力。"""
    cooldown_min: int = 60
    expire_min: int = 10
    reward_snacks: int = 2
    reward_mood: float = 12.0
    exp: int = 8


@dataclass
class AdventureConfig:
    expire_min: int = 10
    help_energy: float = 8.0
    explore_energy: float = 4.0
    deep_energy: float = 6.0
    mood_gain: float = 6.0
    fail_mood_loss: float = 2.0
    success_chance: float = 0.65
    exp: int = 4


@dataclass
class HeartwishConfig:
    active_days: int = 7
    max_people: int = 3
    mood_gain: float = 10.0


@dataclass
class GameConfig:
    # --- 自然变化（每小时） ---
    decay_satiety: float = 2.2
    decay_mood: float = 1.3
    decay_clean: float = 0.9
    regen_energy: float = 6.0

    # --- 难度曲线 ---
    stage_decay_multiplier: Tuple[float, ...] = (1.0, 1.15, 1.3, 1.5, 1.7, 1.85)
    repeat_diminish: float = 0.75     # 连续同一动作，主收益 ×0.75^n
    repeat_min: float = 0.3           # 收益倍率下限
    dirty_clean_threshold: float = 30.0   # 洁净低于此值：
    dirty_growth_penalty: float = 0.5     #   增重减半，套圈战利品也会馊

    # --- 群共同进度（鱼塘等级） ---
    pond_level_exp_step: int = 200    # 每 200 次全群互动升 1 级
    pond_level_max: int = 5
    pond_level_decay_relief: float = 0.04  # 每级自然衰减 -4%

    # --- 失败结束：离家出走 ---
    hunger_zero_grace_hours: int = 36   # 饱腹归零后的宽限
    mood_zero_grace_hours: int = 72     # 心情归零后的宽限

    # --- 胜利结束：出栏 ---
    graduate_weight: float = 80.0
    graduate_mood: float = 60.0
    score_weight: float = 0.4
    score_mood: float = 0.25
    score_clean: float = 0.15
    score_intimacy: float = 0.2
    intimacy_score_cap: float = 100.0
    grade_s: float = 85.0
    grade_a: float = 70.0
    grade_b: float = 55.0
    reward_s: int = 20               # 毕业时第 1 名贡献者的海苔片奖励
    reward_a: int = 12
    reward_b: int = 8
    reward_c: int = 4

    # --- 初始 / 上限 ---
    start_satiety: float = 70.0
    start_mood: float = 70.0
    start_clean: float = 80.0
    start_energy: float = 80.0
    start_weight: float = 8.0
    max_weight: float = 100.0
    start_snacks: int = 0            # 旧配置保留；海苔为绝版库存
    start_rice: int = 5
    daily_bonus_snacks: int = 1      # 旧配置名；补贴发白饭

    feed: FeedAction = field(default_factory=FeedAction)
    snack: SnackAction = field(default_factory=SnackAction)
    pet: PetAction = field(default_factory=PetAction)
    clean: CleanAction = field(default_factory=CleanAction)
    play: PlayAction = field(default_factory=PlayAction)
    weigh: WeighAction = field(default_factory=WeighAction)
    sign: SignConfig = field(default_factory=SignConfig)
    fishing: FishingAction = field(default_factory=FishingAction)
    rescue: RescueConfig = field(default_factory=RescueConfig)
    wish: WishConfig = field(default_factory=WishConfig)
    guess: GuessConfig = field(default_factory=GuessConfig)
    treasure: TreasureConfig = field(default_factory=TreasureConfig)
    adventure: AdventureConfig = field(default_factory=AdventureConfig)
    heartwish: HeartwishConfig = field(default_factory=HeartwishConfig)
