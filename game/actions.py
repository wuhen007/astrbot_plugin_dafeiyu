"""动作文案（全部原创）与纯计算辅助。"""
from __future__ import annotations

FEED_OK = (
    "鱼粮刚撒下去就被一口卷走，腮帮子鼓成了小气球。",
    "追着自己的饭转了三圈，最后一口吸溜进肚。",
    "吃得太急，差点连水花一起咽下去。",
)
FEED_OVER = (
    "肚子已经圆得像乒乓球，再喂真要翻肚皮了！它委屈地吐了个泡。",
    "它扭头就游——摆明了一口也塞不下了。",
)
SNACK_OK = (
    "海苔片入口即化，它幸福地原地转圈。",
    "叼着海苔片冲了个来回，像举着奖杯。",
)
PET_OK = (
    "贴着指尖蹭了蹭，尾巴摇出一串水花。",
    "翻出肚皮求摸摸，完全是故意的。",
    "被逗得吐了一串泡泡，像在咯咯笑。",
)
CLEAN_OK = (
    "新水一注入，它立刻来了个全程冲刺泳。",
    "水质透亮，它舒展鱼鳍打了个滚。",
)
PLAY_OPEN = (
    "圈圈套好！鱼粮就藏在某个泡泡里——猜 1 / 2 / 3。",
    "三个泡泡缓缓升起，其中一个藏着鱼粮。猜几号？",
)
PLAY_WIN = (
    "啪！泡泡应声而破，鱼粮一口入肚，尾巴得意地竖了起来。",
    "精准命中！它叼着战利品绕场一周。",
)
PLAY_MISS = (
    "扑了个空！它歪着脑袋看你，好像在说：就这？",
    "泡泡破了，里面只有一小团水草。它叹了口气。",
)
PLAY_DIRTY = (
    "猜对了……可捞上来的是一颗泥球。它嫌弃地扭过头去。",
    "泡泡里的鱼粮裹满了脏东西，它碰都没碰。",
)
NEW_FISH = (
    "一尾小小的鱼籽晃晃悠悠沉进水里，新的故事开始了。",
    "水面冒出一串泡泡——新住户报到！",
)


def choice(rng, pool) -> str:
    return pool[int(rng.randrange(len(pool)))]


def clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def repeat_multiplier(cfg, repeat_count: int) -> float:
    """连续同一动作的收益倍率：0.75^n，有下限。"""
    return max(cfg.repeat_min, cfg.repeat_diminish ** max(0, repeat_count))
