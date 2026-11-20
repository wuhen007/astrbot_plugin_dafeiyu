"""PIL 实时画卡：上半文本面板 + 下半鱼塘场景。

天机阁同款思路：整张卡片画成图片，规避群里插件对纯文本的改写。
零外部素材，全部代码绘制；中文字体可选（找不到字体时只画图形）。
"""
from __future__ import annotations

import random
import re
from pathlib import Path

from game.config import GameConfig
from game.growth import level_from_exp, stage_name

_W = 640
_SCENE_H = 360
_WATER_CLEAN = (64, 150, 214)
_WATER_DIRTY = (118, 104, 66)
_FISH_BODY = (255, 158, 64)
_FISH_DARK = (206, 106, 28)
_FISH_LIGHT = (255, 202, 130)
_PANEL_BG = (250, 247, 242)
_PANEL_TEXT = (45, 62, 80)
_PANEL_LINE = (228, 222, 212)

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


# PIL + 中文字体（msyh 等）缺这些字形，画进面板前统一替换成字体里有的字符
_CHAR_FIX = {
    "▰": "■", "▱": "□",
    "👑": "★", "♥": "★", "🐟": "", "🎉": "",
    "🥇": "TOP1 ", "🥈": "TOP2 ", "🥉": "TOP3 ",
    "🏆": "★", "💔": "×", "⚠": "!",
}
_EMOJI_RE = re.compile("[\\U0001F000-\\U0001FAFF\\u2600-\\u27BF\\uFE0F\\u2B00-\\u2BFF]")


def _safe_text(line: str) -> str:
    for k, v in _CHAR_FIX.items():
        if k in line:
            line = line.replace(k, v)
    line = _EMOJI_RE.sub("", line)
    line = line.replace("  ", " ").replace("【 ", "【").strip()
    return line


def _load_font(size):
    from PIL import ImageFont
    for p in _TEXT_CANDIDATES:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return None


def render_card_png(state, lines, out_path, config=None, custom_img=None,
                    scene: bool = True) -> str:
    """状态卡：lines 逐行画进文本面板；scene=True 时下方再画鱼塘场景
    （custom_img 提供时贴自定义贴图）。scene=False 则只输出白底文字面板，
    排版完全由渲染决定（不受平台文本改写影响）。"""
    try:
        from PIL import Image, ImageDraw
    except ImportError as e:
        raise ImportError("画卡需要 Pillow：pip install Pillow") from e

    cfg = config or GameConfig()
    fish = state.fish
    # 用状态做种子：同一状态渲染结果稳定
    rng = random.Random(int(fish.satiety * 7 + fish.mood * 13
                            + fish.clean * 29 + fish.weight * 3))
    lines = [str(x).strip() for x in (lines or []) if str(x).strip()]
    font_title, font_s = _load_font(24), _load_font(20)
    wrapped = []
    for i, text in enumerate(lines):
        font = font_title if i == 0 else font_s
        line = ""
        for char in _safe_text(text):
            if font and line and font.getlength(line + char) > _W - 40:
                wrapped.append(line)
                line = ""
            line += char
        wrapped.append(line)
    lines = wrapped

    panel_h = 14 + (len(lines) * 30 + 6 if lines else 0) + 10
    H = panel_h + (_SCENE_H if scene else 0)
    img = Image.new("RGBA", (_W, H), (*_PANEL_BG, 255))
    d = ImageDraw.Draw(img)

    font_title, font_s = _load_font(24), _load_font(20)
    if lines and font_s:
        y = 12
        for i, line in enumerate(lines):
            d.text((20, y), _safe_text(line), font=font_title if i == 0 else font_s,
                   fill=(*_PANEL_TEXT, 255))
            y += 36 if i == 0 else 30
        if scene:
            d.line((0, panel_h - 5, _W, panel_h - 5), fill=(*_PANEL_LINE, 255), width=2)

    if scene:
        _draw_scene(d, img, state, rng, cfg, y_off=panel_h, custom_img=custom_img)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out, "PNG")
    return str(out)


def render_fish_png(state, out_path, config=None) -> str:
    """旧接口：鱼塘场景 + 两行信息面板。"""
    fish = state.fish
    lv = level_from_exp(fish.exp)
    cfg = config or GameConfig()
    lines = [
        f"【🐟 {fish.name} · {stage_name(lv)} Lv.{lv}】",
        f"体重 {fish.weight:.0f}/{cfg.max_weight:.0f} · 心情 {fish.mood:.0f} · 洁净 {fish.clean:.0f}",
    ]
    return render_card_png(state, lines, out_path, config=config)


def _draw_scene(d, img, state, rng, cfg, y_off, custom_img):
    fish = state.fish
    clean_t = max(0.0, min(1.0, fish.clean / 100.0))
    H = y_off + _SCENE_H
    d.rectangle((0, y_off, _W, H), fill=(*_lerp(_WATER_DIRTY, _WATER_CLEAN, clean_t), 255))
    d.ellipse((-80, H - 60, _W + 80, H + 90), fill=(226, 200, 156, 255))
    if fish.clean < 30:
        for _ in range(int((30 - fish.clean) / 6) + 2):
            x, y = rng.randint(30, _W - 30), rng.randint(H - 110, H - 40)
            d.ellipse((x - 6, y - 4, x + 6, y + 4), fill=(105, 88, 58, 255))
    _draw_decorations(d, state.decorations, H)
    if state.end_reason == "runaway":
        _draw_sign(d, y_off)
    else:
        _draw_fish(d, img, fish, rng, cfg, state.end_reason == "graduated",
                   y_off, custom_img)
        if state.end_reason == "graduated":
            _draw_confetti(d, rng, y_off, H)


def _draw_fish(d, img, fish, rng, cfg, graduated, y_off, custom_img):
    if custom_img:
        _paste_sticker(d, img, custom_img, y_off)
        _draw_bubbles(d, fish, y_off + 150)
        return
    w = max(6.0, min(cfg.max_weight, fish.weight))
    L = 120 + w * 2.2                       # 体长随体重
    Hh = L * (0.36 + 0.14 * w / cfg.max_weight)
    cx, cy = 330, y_off + 190
    x0, x1 = cx - L / 2, cx + L / 2
    y0, y1 = cy - Hh / 2, cy + Hh / 2

    # 尾巴（活力高摆得大）→ 背鳍 → 身体 → 肚皮 → 胸鳍
    flap = (0.16 if fish.energy >= 60 else 0.08) * Hh
    tipx = x0 - 40
    d.polygon([(x0 + 6, cy), (tipx, cy - flap - Hh * 0.18), (tipx, cy + flap + Hh * 0.18)],
              fill=(*_FISH_DARK, 255))
    d.polygon([(cx - L * 0.10, y0 + 2), (cx + L * 0.04, y0 - Hh * 0.22), (cx + L * 0.20, y0 + 2)],
              fill=(*_FISH_DARK, 255))
    d.ellipse((x0, y0, x1, y1), fill=(*_FISH_BODY, 255), outline=(*_FISH_DARK, 255), width=4)
    d.ellipse((x0 + L * 0.15, cy - Hh * 0.02, x1 - L * 0.12, y1 - 3), fill=(*_FISH_LIGHT, 255))
    d.polygon([(x0 + L * 0.30, cy + Hh * 0.08), (x0 + L * 0.14, cy + Hh * 0.48),
               (x0 + L * 0.44, cy + Hh * 0.36)], fill=(*_FISH_DARK, 255))

    # 眼睛 + 嘴（表情随心情）
    ex, ey = cx + L * 0.30, cy - Hh * 0.16
    d.ellipse((ex - 11, ey - 11, ex + 11, ey + 11), fill=(255, 255, 255, 255),
              outline=(*_FISH_DARK, 255))
    d.ellipse((ex - 2, ey - 5, ex + 8, ey + 5), fill=(30, 30, 30, 255))
    mx, my = ex + 2, ey + 22
    if fish.mood >= 60:
        d.arc((mx - 15, my - 9, mx + 15, my + 9), 10, 170, fill=(*_FISH_DARK, 255), width=3)
    elif fish.mood >= 30:
        d.line((mx - 10, my, mx + 10, my), fill=(*_FISH_DARK, 255), width=3)
    else:
        d.arc((mx - 15, my - 9, mx + 15, my + 9), 190, 350, fill=(*_FISH_DARK, 255), width=3)
        d.ellipse((mx + 20, my - 26, mx + 30, my - 10), fill=(120, 190, 240, 255))   # 汗滴

    _draw_bubbles(d, fish, ey)

    # 冲刺线（活力高）
    if fish.energy >= 60:
        for i in range(3):
            yy = cy - Hh * 0.2 + i * 14
            d.line((tipx - 18, yy, tipx - 44, yy), fill=(240, 250, 255, 255), width=3)

    # 饿了：头顶冒 "?" 思考泡
    if fish.satiety < 30:
        tx, ty = cx + L * 0.36, cy - Hh * 0.55
        for ddx, ddy, r in ((0, 0, 3), (12, -12, 5), (26, -26, 9)):
            d.ellipse((tx + ddx - r, ty + ddy - r, tx + ddx + r, ty + ddy + r),
                      fill=(240, 250, 255, 255))
        f = _load_font(16)
        if f:
            d.text((tx + 20, ty - 36), "?", font=f, fill=(*_PANEL_TEXT, 255))

    # 毕业戴小礼帽（顶在头顶）
    if graduated:
        hx, hy = ex - 2, y0 + 2
        d.polygon([(hx - 14, hy), (hx + 18, hy), (hx + 14, hy - 16), (hx - 10, hy - 16)],
                  fill=(40, 40, 46, 255))
        d.line((hx - 24, hy, hx + 28, hy), fill=(40, 40, 46, 255), width=5)


def _draw_bubbles(d, fish, ey):
    """吐泡泡（数量随心情）。"""
    for i in range(1 + int(fish.mood // 40)):
        bx, by, r = 330 + 26 + i * 16, ey - 26 - i * 20, 4 + i * 2
        d.ellipse((bx - r, by - r, bx + r, by + r), outline=(240, 250, 255, 255), width=2)


def _paste_sticker(d, img, custom_img, y_off):
    """自定义鱼形象贴图：等比缩放贴进水域中央；失败则静默交给调用方回退。"""
    try:
        from PIL import Image as PILImage
        sticker = PILImage.open(custom_img).convert("RGBA")
        max_w, max_h = 300, 220
        ratio = min(max_w / max(1, sticker.width), max_h / max(1, sticker.height), 1.0)
        sticker = sticker.resize((max(1, int(sticker.width * ratio)),
                                  max(1, int(sticker.height * ratio))))
        cx, cy = 330, y_off + 195
        img.alpha_composite(sticker, (int(cx - sticker.width / 2),
                                      int(cy - sticker.height / 2)))
    except Exception:
        pass


def _draw_confetti(d, rng, y_off, H):
    colors = ((255, 99, 132), (255, 214, 79), (89, 205, 144), (108, 178, 255), (200, 140, 255))
    for _ in range(26):
        x, y = rng.randint(10, _W - 10), rng.randint(y_off + 10, H - 80)
        c = colors[rng.randrange(len(colors))]
        d.rectangle((x - 2, y - 2, x + 2, y + 2), fill=(*c, 255))


def _draw_sign(d, y_off):
    d.rectangle((250, y_off + 20, 390, y_off + 100), fill=(214, 178, 120, 255),
                outline=(140, 106, 60, 255), width=4)
    d.rectangle((316, y_off + 100, 324, y_off + 190), fill=(140, 106, 60, 255))
    font = _load_font(22)
    if font:
        d.text((268, y_off + 42), "…游走了…", font=font, fill=(90, 60, 30, 255))
    for i in range(4):
        bx, by, r = 430 + i * 30, y_off + 40 - i * 22, max(2, 8 - i)
        d.ellipse((bx - r, by - r, bx + r, by + r), outline=(240, 250, 255, 255), width=2)


def _draw_decorations(d, decorations, bottom):
    from game.decorations import completed
    built = completed(decorations)
    if "水草角" in built:
        for x, height in ((42, 90), (62, 120), (82, 74), (102, 102)):
            d.line([(x, bottom - 25), (x - 12, bottom - height // 2), (x + 5, bottom - height)],
                   fill=(65, 194, 119, 255), width=9)
            d.ellipse((x - 25, bottom - height // 2 - 12, x, bottom - height // 2), fill=(103, 221, 137, 255))
    if "贝壳小屋" in built:
        x, y = 510, bottom - 34
        d.pieslice((x - 65, y - 96, x + 65, y + 24), 180, 360,
                   fill=(255, 205, 197, 255), outline=(206, 128, 140, 255), width=4)
        for dx in (-42, -21, 0, 21, 42):
            d.line((x, y + 3, x + dx, y - 52), fill=(220, 157, 158, 255), width=3)
        d.rounded_rectangle((x - 14, y - 27, x + 14, y + 6), radius=12, fill=(87, 91, 129, 255))
    if "月亮灯" in built:
        d.line((564, bottom - 35, 564, bottom - 252), fill=(230, 214, 169, 255), width=5)
        d.line((564, bottom - 252, 528, bottom - 252), fill=(230, 214, 169, 255), width=5)
        d.ellipse((502, bottom - 250, 548, bottom - 204), fill=(255, 235, 143, 255))
        d.ellipse((519, bottom - 252, 550, bottom - 220), fill=(71, 145, 195, 255))


def render_memorial_png(page, out_path, config=None):
    from game.engine import memorial_lines
    from game.state import FishState, PondState
    snapshot = page.get("memorial")
    if snapshot is None:
        return render_card_png(PondState(), ["鱼塘纪念册", *memorial_lines(page)], out_path, scene=False)
    if not isinstance(snapshot, dict):
        return render_card_png(PondState(), ["鱼塘纪念册", *memorial_lines(page)], out_path, scene=False)
    fish = snapshot.get("fish")
    decorations = snapshot.get("decorations")
    state = PondState(fish=FishState.from_dict(fish if isinstance(fish, dict) else {}),
                      end_reason=page.get("reason"),
                      decorations=decorations if isinstance(decorations, dict) else {})
    return render_card_png(state, ["鱼塘纪念册", *memorial_lines(page)], out_path, config=config)
