"""PIL 实时画鱼：体型随体重、表情随心情、水色随洁净。

零外部素材，全部代码绘制（完全原创）；中文字体可选，
找不到字体时只画图形不写文字。
"""
from __future__ import annotations

import random
from pathlib import Path

from game.config import GameConfig
from game.growth import level_from_exp, stage_name

_W, _H = 640, 400
_WATER_CLEAN = (64, 150, 214)
_WATER_DIRTY = (118, 104, 66)
_FISH_BODY = (255, 158, 64)
_FISH_DARK = (206, 106, 28)
_FISH_LIGHT = (255, 202, 130)

_TEXT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyh.ttf",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
)


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _load_font(size):
    from PIL import ImageFont
    for p in _TEXT_CANDIDATES:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return None


def render_fish_png(state, out_path, config=None) -> str:
    """按当前状态画一张鱼塘图，保存为 PNG 并返回路径。"""
    try:
        from PIL import Image, ImageDraw
    except ImportError as e:
        raise ImportError("画鱼需要 Pillow：pip install Pillow") from e

    cfg = config or GameConfig()
    fish = state.fish
    # 用状态做种子：同一状态渲染结果稳定
    rng = random.Random(int(fish.satiety * 7 + fish.mood * 13 + fish.clean * 29 + fish.weight * 3))
    clean_t = max(0.0, min(1.0, fish.clean / 100.0))

    img = Image.new("RGB", (_W, _H), _lerp(_WATER_DIRTY, _WATER_CLEAN, clean_t))
    d = ImageDraw.Draw(img)

    # 水底沙地；水脏时漂垃圾
    d.ellipse((-80, _H - 60, _W + 80, _H + 90), fill=(226, 200, 156))
    if fish.clean < 30:
        for _ in range(int((30 - fish.clean) / 6) + 2):
            x, y = rng.randint(30, _W - 30), rng.randint(_H - 110, _H - 40)
            d.ellipse((x - 6, y - 4, x + 6, y + 4), fill=(105, 88, 58))

    if state.end_reason == "runaway":
        _draw_sign(d)
    else:
        _draw_fish(d, fish, rng, cfg, state.end_reason == "graduated")
        if state.end_reason == "graduated":
            _draw_confetti(d, rng)

    # 顶部/底部信息文字（有中文字体才写）
    font_s, font_m = _load_font(20), _load_font(26)
    if font_m:
        lv = level_from_exp(fish.exp)
        d.text((18, 14), f"{fish.name} · {stage_name(lv)} Lv.{lv}", font=font_m,
               fill=(255, 255, 255), stroke_width=2, stroke_fill=(24, 48, 70))
        d.text((18, _H - 42),
               f"体重 {fish.weight:.0f}/{cfg.max_weight:.0f} · 心情 {fish.mood:.0f} · 洁净 {fish.clean:.0f}",
               font=font_s, fill=(255, 255, 255), stroke_width=2, stroke_fill=(24, 48, 70))

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")
    return str(out)


def _draw_fish(d, fish, rng, cfg, graduated):
    w = max(6.0, min(cfg.max_weight, fish.weight))
    L = 120 + w * 2.2                       # 体长随体重
    Hh = L * (0.36 + 0.14 * w / cfg.max_weight)
    cx, cy = 330, 205
    x0, x1 = cx - L / 2, cx + L / 2
    y0, y1 = cy - Hh / 2, cy + Hh / 2

    # 尾巴（活力高摆得大）→ 背鳍 → 身体 → 肚皮 → 胸鳍
    flap = (0.16 if fish.energy >= 60 else 0.08) * Hh
    tipx = x0 - 40
    d.polygon([(x0 + 6, cy), (tipx, cy - flap - Hh * 0.18), (tipx, cy + flap + Hh * 0.18)], fill=_FISH_DARK)
    d.polygon([(cx - L * 0.10, y0 + 2), (cx + L * 0.04, y0 - Hh * 0.22), (cx + L * 0.20, y0 + 2)], fill=_FISH_DARK)
    d.ellipse((x0, y0, x1, y1), fill=_FISH_BODY, outline=_FISH_DARK, width=4)
    d.ellipse((x0 + L * 0.15, cy - Hh * 0.02, x1 - L * 0.12, y1 - 3), fill=_FISH_LIGHT)
    d.polygon([(x0 + L * 0.30, cy + Hh * 0.08), (x0 + L * 0.14, cy + Hh * 0.48), (x0 + L * 0.44, cy + Hh * 0.36)],
              fill=_FISH_DARK)

    # 眼睛 + 嘴（表情随心情）
    ex, ey = cx + L * 0.30, cy - Hh * 0.16
    d.ellipse((ex - 11, ey - 11, ex + 11, ey + 11), fill=(255, 255, 255), outline=_FISH_DARK)
    d.ellipse((ex - 2, ey - 5, ex + 8, ey + 5), fill=(30, 30, 30))
    mx, my = ex + 2, ey + 22
    if fish.mood >= 60:
        d.arc((mx - 15, my - 9, mx + 15, my + 9), 10, 170, fill=_FISH_DARK, width=3)
    elif fish.mood >= 30:
        d.line((mx - 10, my, mx + 10, my), fill=_FISH_DARK, width=3)
    else:
        d.arc((mx - 15, my - 9, mx + 15, my + 9), 190, 350, fill=_FISH_DARK, width=3)
        d.ellipse((mx + 20, my - 26, mx + 30, my - 10), fill=(120, 190, 240))   # 汗滴

    # 吐泡泡（数量随心情）+ 冲刺线（活力高）
    for i in range(1 + int(fish.mood // 40)):
        bx, by, r = ex + 26 + i * 16, ey - 26 - i * 20, 4 + i * 2
        d.ellipse((bx - r, by - r, bx + r, by + r), outline=(240, 250, 255), width=2)
    if fish.energy >= 60:
        for i in range(3):
            yy = cy - Hh * 0.2 + i * 14
            d.line((tipx - 18, yy, tipx - 44, yy), fill=(240, 250, 255), width=3)

    # 饿了：头顶冒 "?" 思考泡
    if fish.satiety < 30:
        tx, ty = cx + L * 0.36, cy - Hh * 0.55
        for ddx, ddy, r in ((0, 0, 3), (12, -12, 5), (26, -26, 9)):
            d.ellipse((tx + ddx - r, ty + ddy - r, tx + ddx + r, ty + ddy + r), fill=(240, 250, 255))
        f = _load_font(16)
        if f:
            d.text((tx + 20, ty - 36), "?", font=f, fill=(24, 48, 70))

    # 毕业戴小礼帽（顶在头顶）
    if graduated:
        hx, hy = ex - 2, y0 + 2
        d.polygon([(hx - 14, hy), (hx + 18, hy), (hx + 14, hy - 16), (hx - 10, hy - 16)], fill=(40, 40, 46))
        d.line((hx - 24, hy, hx + 28, hy), fill=(40, 40, 46), width=5)


def _draw_confetti(d, rng):
    colors = ((255, 99, 132), (255, 214, 79), (89, 205, 144), (108, 178, 255), (200, 140, 255))
    for _ in range(26):
        x, y = rng.randint(10, _W - 10), rng.randint(10, _H - 90)
        c = colors[rng.randrange(len(colors))]
        d.rectangle((x - 2, y - 2, x + 2, y + 2), fill=c)


def _draw_sign(d):
    d.rectangle((250, 130, 390, 210), fill=(214, 178, 120), outline=(140, 106, 60), width=4)
    d.rectangle((316, 210, 324, 300), fill=(140, 106, 60))
    font = _load_font(22)
    if font:
        d.text((268, 152), "…游走了…", font=font, fill=(90, 60, 30))
    for i in range(4):
        bx, by, r = 430 + i * 30, 150 - i * 22, max(2, 8 - i)
        d.ellipse((bx - r, by - r, bx + r, by + r), outline=(240, 250, 255), width=2)
