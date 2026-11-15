"""把 ActionResult 渲染成文本卡：纯文本（终端/降级用）与 QQ markdown 两种。"""
from __future__ import annotations

from typing import Dict, List

_STAT_KEYS = (("饱腹", "satiety"), ("心情", "mood"), ("洁净", "clean"), ("活力", "energy"))


def bar(value: float, width: int = 10, max_value: float = 100.0) -> str:
    if max_value <= 0:
        return "▱" * width
    filled = max(0, min(width, int(value / max_value * width + 0.5)))
    return "▰" * filled + "▱" * (width - filled)


def _fmt(v: float) -> str:
    """数值变化格式化：整数不带小数点，带正负号。"""
    v = round(v, 1)
    if abs(v - round(v)) < 1e-9:
        return f"{int(round(v)):+d}"
    return f"{v:+.1f}"


def delta_line(deltas: Dict[str, float]) -> str:
    return " · ".join(f"{k} {_fmt(v)}" for k, v in deltas.items() if abs(v) > 1e-9)


def buttons_line(result) -> str:
    return " ".join(f"[{b.label}]" for b in result.buttons)


# ---------------------------------------------------------------- 纯文本
def _panel_lines(result) -> List[str]:
    d = result.data
    mw = d.get("max_weight", 100.0)
    lines = [f"🐟 {d['name']} · {d['stage']}（{d['period']}）"]
    lines += result.lines
    for label, key in _STAT_KEYS:
        lines.append(f"{label} {bar(d[key])} {d[key]}/100")
    lines.append(f"体重 {bar(d['weight'], max_value=mw)} {d['weight']}/{mw}")
    if d.get("exp_need"):
        lines.append(f"成长 Lv.{d['level']} {d['stage']} · 经验 {d['exp_cur']}/{d['exp_need']} "
                     f"{bar(d['exp_cur'], width=6, max_value=d['exp_need'])}")
    else:
        lines.append(f"成长 Lv.{d['level']} {d['stage']} · 经验 {d['exp_cur']}（已满阶）")
    step = d.get("pond_exp_need") or 0
    if step:
        lines.append(f"鱼塘 Lv.{d['pond_level']} · 全群互动 {d['pond_exp_cur']}/{step} "
                     f"{bar(d['pond_exp_cur'], width=6, max_value=step)}")
    if d.get("offline"):
        lines.append(f"（这段时间：{delta_line(d['offline'])}）")
    if d.get("top_keeper"):
        lines.append(f"👑 最上心的鱼友：{d['top_keeper']}（亲密 {d['top_intimacy']}）")
    else:
        lines.append("👑 还没有鱼友登榜")
    if d.get("eligible"):
        lines.append("🎉 已达成出栏条件！可发「出栏」毕业。")
    lines.append(buttons_line(result))
    return lines


def _plain_lines(result) -> List[str]:
    if result.event == "panel":
        return _panel_lines(result)
    if result.event in ("graduated", "runaway"):
        out = [f"══════ {result.title} ══════", *result.lines]
    else:
        icon = "▶" if result.ok else "⚠"
        out = [f"{icon} {result.title}", *result.lines]
        dl = delta_line(result.deltas)
        if dl:
            out.append(dl)
    out.append(buttons_line(result))
    return out


def render_plain(result) -> str:
    return "\n".join(_plain_lines(result))


# ---------------------------------------------------------------- QQ markdown
def _md_panel_lines(result) -> List[str]:
    d = result.data
    flavor = result.lines[0] if result.lines else ""
    lines = [f"# 🐟 {d['name']} · {d['stage']}", f"**{d['period']}** · {flavor}"]
    for label, key in _STAT_KEYS:
        lines.append(f"**{label}** {bar(d[key])} {d[key]}/100")
    if d.get("exp_need"):
        lines.append(f"**成长** Lv.{d['level']} {d['stage']} · 经验 {d['exp_cur']}/{d['exp_need']}")
    else:
        lines.append(f"**成长** Lv.{d['level']} {d['stage']} · 已满阶")
    lines.append(f"**体重** {d['weight']}/{d.get('max_weight', 100.0)} · "
                 f"**鱼塘** Lv.{d['pond_level']} · 全群互动 {d['pond_exp_cur']}/{d['pond_exp_need']}")
    if d.get("top_keeper"):
        lines.append(f"👑 最上心的鱼友：{d['top_keeper']}（亲密 {d['top_intimacy']}）")
    if d.get("eligible"):
        lines.append("🎉 已达成出栏条件！可发「出栏」毕业。")
    lines.append(buttons_line(result))
    return lines


def render_markdown(result) -> str:
    if result.event == "panel":
        body = _md_panel_lines(result)
    elif result.event in ("graduated", "runaway"):
        body = [f"# 🎉 {result.title}", *result.lines, buttons_line(result)]
    else:
        body = [f"# {result.title}", *result.lines]
        dl = delta_line(result.deltas)
        if dl:
            body.append(f"**变化**：{dl}")
        body.append(buttons_line(result))
    return "\n".join(body)
