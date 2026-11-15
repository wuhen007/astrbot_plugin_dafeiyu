#!/usr/bin/env python3
"""《肥鱼养成记》终端模拟器 —— 不开 QQ 玩通完整流程。

用法：
  python sim.py                 交互游玩（真实时间流逝，`等 5` 快进 5 小时）
  python sim.py --auto          自动演示：从鱼籽玩到出栏结算（胜利结局）
  python sim.py --auto-neglect  自动演示：无人照顾 → 离家出走（失败结局）
  --md                          用 markdown 卡片渲染（预览 QQ 卡片效果）
  --data DIR                    存档目录（默认 data/sim）
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from game import GameConfig, JsonStore, load_or_create  # noqa: E402
from game.engine import Pond  # noqa: E402
from render import render_fish_png, render_markdown, render_plain  # noqa: E402
from render.textcard import delta_line  # noqa: E402


class SimClock:
    """交互模式时钟：起点是真实当前时间，可用 `等 N` 快进。"""

    def __init__(self):
        self.now = time.time()

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeClock:
    """自动模式时钟：固定起点，全程快进。"""

    def __init__(self):
        self.now = 1_700_000_000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def show_card(pond, result, use_md):
    print(render_markdown(result) if use_md else render_plain(result))
    print("-" * 46)


def try_image(pond, path):
    try:
        p = render_fish_png(pond.state, str(path), config=pond.cfg)
        print(f"[鱼图已保存：{p}]")
    except ImportError as e:
        print(f"[跳过画图：{e}]")


def interactive(cfg: GameConfig, data_dir: Path, use_md: bool):
    store = JsonStore(data_dir / "ponds")
    clock = SimClock()
    pond = load_or_create(store, "sim-local", config=cfg, clock=clock, rng=random.Random())
    print("《肥鱼养成记》终端版。指令：养鱼/投喂/加餐/逗鱼/净水/玩耍 [1-3]/称重/排行/档案/出栏/再养一条/帮助")
    print("彩蛋：`等 5` 快进 5 小时；`退出` 结束。")
    while True:
        try:
            line = input("🐟> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in ("退出", "exit", "quit"):
            break
        if line.startswith("等"):
            try:
                hours = float(line[1:].strip() or "1")
            except ValueError:
                hours = 1.0
            clock.advance(hours * 3600)
            print(f"[时间快进 {hours:g} 小时]")
            continue
        parts = line.split()
        result = pond.handle("sim-user", "终端玩家", parts[0],
                             parts[1] if len(parts) > 1 else None)
        show_card(pond, result, use_md)
    try_image(pond, data_dir / "fish.png")


def auto(cfg: GameConfig, data_dir: Path, use_md: bool, neglect: bool):
    clock, rng = FakeClock(), random.Random(42)
    pond = Pond.create("sim-auto", config=cfg, clock=clock, rng=rng)
    r = pond.handle("auto", "自动玩家", "养鱼")
    show_card(pond, r, use_md)

    if neglect:
        hours = 0
        while pond.state.status == "alive" and hours < 200:
            clock.advance(6 * 3600)
            hours += 6
            r = pond.handle("auto", "自动玩家", "养鱼")
        show_card(pond, r, use_md)
        try_image(pond, data_dir / "fish.png")
        return

    # 照料策略：饿→投喂 / 脏→净水 / 闷→逗鱼 / 有体力→玩耍 / 达标→出栏
    p = pond.cfg
    steps = 0
    while pond.state.status == "alive" and steps < 400:
        steps += 1
        fish = pond.state.fish
        # 达标确认前先 touch 刷新（自然衰减/求援过期都可能改变数值）
        if pond._eligible():
            pond.handle("auto", "自动玩家", "养鱼")
            if pond._eligible():
                r = pond.handle("auto", "自动玩家", "出栏")
                show_card(pond, r, use_md)
                break
        # 求援事件：两位虚拟鱼友协作完成（转危为机，还加心情）
        if pond.state.pending_rescue and pond.state.status == "alive":
            pond.handle("auto", "自动玩家", "救援")
            r = pond.handle("auto2", "二号鱼友", "救援")
            if r.event == "rescue":
                print(f"[{steps:03d}] 救援成功  {delta_line(r.deltas)}")
            continue
        if fish.satiety < 55:
            cmd, cd = "投喂", p.feed.cooldown_min
        elif fish.clean < 45:
            cmd, cd = "净水", p.clean.cooldown_min
        elif fish.mood < 68:
            cmd, cd = "逗鱼", p.pet.cooldown_min
        elif fish.energy >= p.play.vitality_cost and \
                pond._cooldown_left("play", p.play.cooldown_min) == 0:
            cmd, cd = "玩耍", p.play.cooldown_min
        else:
            clock.advance(3600)
            pond._touch()          # 空转也要触发引擎 tick，让自然衰减/回复生效
            continue
        if cmd == "玩耍":
            pond.handle("auto", "自动玩家", "玩耍")            # 开局
            if pond.state.pending_play:
                r = pond.handle("auto", "自动玩家", "玩耍", str(rng.randrange(3) + 1))
            else:
                r = pond.handle("auto", "自动玩家", "养鱼")
        else:
            r = pond.handle("auto", "自动玩家", cmd)
        if r.event == "levelup" or steps % 10 == 0:
            show_card(pond, r, use_md)
        else:
            print(f"[{steps:03d}] {r.title}  {delta_line(r.deltas)}")
        clock.advance((cd + 1) * 60)
    try_image(pond, data_dir / "fish.png")


def main():
    ap = argparse.ArgumentParser(description="《肥鱼养成记》终端模拟器")
    ap.add_argument("--auto", action="store_true", help="自动演示到毕业出栏")
    ap.add_argument("--auto-neglect", action="store_true", help="自动演示离家出走结局")
    ap.add_argument("--md", action="store_true", help="用 markdown 卡片渲染（预览 QQ 效果）")
    ap.add_argument("--data", default=str(ROOT / "data" / "sim"), help="存档目录")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    data_dir = Path(args.data)
    data_dir.mkdir(parents=True, exist_ok=True)
    if args.auto or args.auto_neglect:
        auto(GameConfig(), data_dir, args.md, neglect=args.auto_neglect)
    else:
        interactive(GameConfig(), data_dir, args.md)


if __name__ == "__main__":
    main()
