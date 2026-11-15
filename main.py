"""AstrBot 插件入口：《肥鱼养成记》。

发送模式（与 zhutian ui_kit 同款，在本机 bot 实测可用）：
- 每次动作两条消息：①鱼图（image_result；**按钮交互事件不发图**——QQ 会拒绝
  交互事件的图片被动回复）②「文本 + 键盘」：Plain(md_text) + ActionRow(Button...)
  + use_markdown(True)（msg_type=2 MarkdownPayload + keyboard）
- 按钮：Button(id 唯一, label≤16, Button.CallbackAction(data=dict), style)；
  键盘 ≤5 行×每行 5 钮；ActionRow 自带 fallback_text（按钮过期提示文字指令），
  action_timeout=600s
- 无按钮组件（旧版 AstrBot）→ 整体退化为纯文本（msg_type=0，\n 原生换行）
- 指令：养鱼（别名：投喂/加餐/逗鱼/净水/玩耍/称重/排行/档案/出栏/再养一条/帮助，
  含 / 前缀变体）
"""
from __future__ import annotations

import asyncio
import hashlib
import random
import re
import sys
import time
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context, Star, register

PLUGIN_DIR = Path(__file__).resolve().parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from game import COMMANDS, GameConfig, JsonStore, load_or_create          # noqa: E402
from render import render_card_png, render_markdown, render_plain         # noqa: E402
from render.textcard import delta_line                                    # noqa: E402

# ---------------------------------------------------------------- 版本兼容层
# v4.29.x：astrbot.api.event.filter 包提供 command / button_action 独立装饰器；
# v3：astrbot.api.event 下是 FilterBuilder 实例（filter.command 可用，无按钮）
_button_dec = None
try:
    from astrbot.api.event.filter import button_action as _button_dec     # noqa: E402
except ImportError:
    pass

_command_dec = None
try:
    from astrbot.api.event.filter import command as _command_dec          # noqa: E402
except ImportError:
    try:
        from astrbot.api.event import filter as _v3_filter                # noqa: E402
        _command_dec = _v3_filter.command
    except ImportError:
        pass

Plain = Image = ActionRow = Button = CallbackAction = None
try:
    from astrbot.api.message_components import ActionRow, Button, Image, Plain  # noqa: E402
except ImportError:
    try:
        from astrbot.api.message_components import Image, Plain           # noqa: E402
    except ImportError:
        pass
if Button is not None:
    # v4.29.x：CallbackAction/UrlAction/Style 是 Button 的内部类，须 Button.X；
    # 未来版本可能改为顶层导出，两种都试。
    try:
        from astrbot.api.message_components import CallbackAction as _CA  # noqa: E402
        CallbackAction = _CA
    except ImportError:
        CallbackAction = getattr(Button, "CallbackAction", None)
_HAS_BUTTONS = all(c is not None for c in (ActionRow, Button, CallbackAction))

# ---------------------------------------------------------------- 常量
_ACTION_KEYS = ("投喂", "加餐", "逗鱼", "净水", "玩耍", "称重", "排行", "排行榜",
                "档案", "图鉴", "出栏", "再养一条", "帮助")
# 斜杠前缀变体：QQ 官方 bot 用户习惯 /指令；若 AstrBot 唤醒前缀没配 "/"，命令匹配也能吃到
_ALIAS_NAMES = set(_ACTION_KEYS) | {f"/{k}" for k in _ACTION_KEYS}
_ACTION_RE = re.compile("(投喂|加餐|逗鱼|净水|玩耍|称重|排行|排行榜|档案|图鉴|出栏|再养一条|帮助)")
_SHELL_RE = re.compile("玩耍\\s*([123①②③])")
_BTN_PREFIX = "dfy:"
_DIFF_SCALE = {"休闲": 0.7, "标准": 1.0, "硬核": 1.4}
_BTN_TIMEOUT = 600          # 按钮有效期（秒），过期走 fallback_text 提示文字指令
_LABEL_MAX = 16
_ID_MAX = 64

logger.info("[肥鱼养成记] 模块导入完成：按钮组件=%s 指令装饰器=%s 按钮装饰器=%s",
            _HAS_BUTTONS, _command_dec is not None, _button_dec is not None)


def md_text(text: str) -> str:
    """QQ 原生 md 渲染规则：单个 \\n 不换行 → 行尾补两空格强制换行；
    【】整行/# 开头行加粗。与 zhutian ui_kit.md_text 同款（实测可用）。"""
    lines = str(text or "").split("\n")
    out = []
    for i, line in enumerate(lines):
        s = line.rstrip()
        if not s:
            out.append("")
            continue
        if (s.startswith("【") and s.endswith("】")) or s.startswith("# "):
            s = f"**{s}**"
        if i < len(lines) - 1 and s:
            s += "  "
        out.append(s)
    return "\n".join(out)


def _default_data_dir() -> Path:
    """存档目录：优先 StarTools，回退到 AstrBot 运行目录下的 data/plugin_data。"""
    try:
        from astrbot.api.star import StarTools
        p = StarTools.get_data_dir("astrbot_plugin_dafeiyu")
        if isinstance(p, (str, Path)):
            return Path(p)
    except Exception:
        pass
    return Path("data") / "plugin_data" / "astrbot_plugin_dafeiyu"


def _make_yangyu_decorator():
    """「养鱼」主指令装饰器；兼容 v3/v4 的 alias 参数差异。"""
    if _command_dec is None:
        logger.warning("[肥鱼养成记] 未找到指令装饰器，文字指令不可用（仅按钮回调可用）")
        return lambda f: f
    try:
        return _command_dec("养鱼", alias=_ALIAS_NAMES)
    except TypeError:
        return _command_dec("养鱼")


@register("astrbot_plugin_dafeiyu", "wuhen007",
          "肥鱼养成记：全群共养一条大肥鱼，养成出栏结算", "v1.0.4")
class DaFeiYuPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        self.context = context
        raw = dict(config) if config is not None else {}
        self._md_mode = raw.get("output_mode", "markdown") == "markdown"
        self._send_image = raw.get("send_image", True)
        scale = _DIFF_SCALE.get(raw.get("difficulty", "标准"), 1.0)
        self._cfg = GameConfig()
        if scale != 1.0:
            self._cfg.decay_satiety *= scale
            self._cfg.decay_mood *= scale
            self._cfg.decay_clean *= scale
        data_dir = Path(raw.get("data_dir") or _default_data_dir())
        self._store = JsonStore(data_dir / "ponds")
        self._image_dir = data_dir / "images"
        self._custom_img_dir = data_dir / "fish_imgs"   # 丢图即用：自定义鱼形象
        self._ponds = {}
        self._locks = {}
        logger.info("[肥鱼养成记] 已加载：输出=%s 画图=%s 难度=%s 存档=%s",
                    "markdown" if self._md_mode else "text", self._send_image,
                    raw.get("difficulty", "标准"), data_dir)

    # ------------------------------------------------------------ 内部
    def _get_pond(self, event) -> Pond:
        session = event.unified_msg_origin
        pond = self._ponds.get(session)
        if pond is None:
            pond = load_or_create(self._store, session, config=self._cfg,
                                  clock=time.time, rng=random.Random())
            self._ponds[session] = pond
        return pond

    def _pick_custom_img(self):
        """自定义鱼形象目录（fish_imgs/）里随机取一张；没有则 None。"""
        try:
            if self._custom_img_dir.is_dir():
                imgs = [p for p in self._custom_img_dir.iterdir()
                        if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp")]
                if imgs:
                    return str(random.choice(imgs))
        except Exception as e:
            logger.warning(f"[肥鱼养成记] 自定义鱼图读取失败，改用内置画鱼：{e}")
        return None

    def _build_card_image(self, session: str, pond: Pond, result) -> str:
        """整张状态卡画成图片：文本面板（卡片文案去按钮提示行）+ 鱼塘场景。"""
        lines = [ln for ln in render_markdown(result).split("\n")
                 if not ln.startswith("[")]
        custom = self._pick_custom_img()
        h = hashlib.md5(session.encode("utf-8")).hexdigest()[:10]
        return render_card_png(pond.state, lines, str(self._image_dir / f"{h}.png"),
                               config=pond.cfg, custom_img=custom)

    def _one_liner(self, result) -> str:
        """键盘消息的正文：单行摘要（面板详情在图里），压平插件改不动它。"""
        if result.event == "panel":
            d = result.data
            return md_text(f"【🐟 {d['name']} · {d['stage']}】点按钮互动，发「帮助」看玩法")
        if result.event in ("graduated", "runaway"):
            s = (result.data or {}).get("summary", {})
            return md_text(f"【{result.title}】评级 {s.get('grade', 'C')} · 详情见图")
        first = result.lines[0] if result.lines else ""
        base = f"【{result.title}】{first}"
        dl = delta_line(result.deltas)
        if dl:
            base = f"{base} ｜ {dl}"
        return md_text(base)

    def _build_rows(self, result):
        """引擎按钮 → ActionRow（每行 3 个）。data 用 dict {"act","arg"}；
        id 用 act|arg|label 组合保证同键盘唯一；无按钮组件返回空。"""
        if not _HAS_BUTTONS:
            return []
        style_cls = getattr(Button, "Style", None)
        style_default = getattr(style_cls, "DEFAULT", None) if style_cls else None
        items = []
        for b in result.buttons:
            parts = b.command.split()
            if parts and parts[0] == "养鱼":
                parts = parts[1:]
            act = parts[0] if parts else "养鱼"
            arg = parts[1] if len(parts) > 1 else None
            label = str(b.label)[:_LABEL_MAX]
            ident = f"{_BTN_PREFIX}{act}|{arg or ''}|{label}"[:_ID_MAX]
            items.append((ident, label, {"act": act, "arg": arg}))
        rows = []
        for i in range(0, len(items), 3):
            chunk = items[i:i + 3]
            buttons = []
            for ident, label, data in chunk:
                kw = {"id": ident, "label": label,
                      "action": CallbackAction(data=data)}
                if style_default is not None:
                    kw["style"] = style_default
                buttons.append(Button(**kw))
            rows.append(ActionRow(
                buttons=buttons,
                fallback_text=" / ".join(f"[{l}]" for _, l, _ in chunk)
                + "（按钮失效请发送文字指令）",
                action_timeout=_BTN_TIMEOUT))
        return rows

    def _build_result(self, event, result):
        """「单行摘要 + 键盘」消息：markdown 模式且有按钮组件走 msg_type=2；
        否则纯文本全量卡片（msg_type=0，\\n 原生换行）。图片不在这里发。"""
        rows = self._build_rows(result) if self._md_mode else []
        if rows:
            res = event.chain_result([Plain(self._one_liner(result)), *rows])
            try:
                res.use_markdown(True)
            except Exception:
                pass    # 极旧版 MessageChain 无 use_markdown，跟随平台默认
            return res
        return event.plain_result(render_plain(result))

    async def _dispatch(self, event, command: str, arg=None):
        try:
            uid = str(event.get_sender_id() or "anon")
            name = (event.get_sender_name() or "").strip() or "鱼友"
            session = event.unified_msg_origin
            lock = self._locks.setdefault(session, asyncio.Lock())
            async with lock:
                pond = self._get_pond(event)
                result = pond.handle(uid, name, command, arg)
            # ① 状态卡图：按钮交互事件的图片被动回复会被 QQ 拒绝，一律跳过
            if self._send_image and Image is not None \
                    and event.get_button_interaction() is None:
                try:
                    img_path = self._build_card_image(session, pond, result)
                    yield event.image_result(img_path)
                except Exception as e:
                    logger.warning(f"[肥鱼养成记] 画卡失败，跳过：{e}")
            # ② 文本 + 键盘
            yield self._build_result(event, result)
        except Exception as e:
            logger.exception("[肥鱼养成记] 处理指令 %r 失败", command)
            try:
                yield event.plain_result(f"[肥鱼养成记] 内部错误：{type(e).__name__}: {e}")
            except Exception:
                pass

    async def _yangyu(self, event):
        """「养鱼」及全部别名指令：从原文里解析子动作（兼容各种唤醒前缀）。"""
        text = event.message_str or ""
        logger.info("[肥鱼养成记] 收到指令 message_str=%r", text)
        sub, arg = "养鱼", None
        m = _SHELL_RE.search(text)
        act = _ACTION_RE.search(text)
        if m:
            sub, arg = "玩耍", m.group(1)
        elif act:
            sub = act.group(1)
        async for res in self._dispatch(event, sub, arg):
            yield res

    # ------------------------------------------------------------ 按钮回调
    if _button_dec is not None:
        @_button_dec()
        async def on_button(self, event: AstrMessageEvent):
            try:
                inter = event.get_button_interaction()
            except Exception:
                inter = None
            if inter is None:
                return
            aid = getattr(inter, "action_id", "") or ""
            if not aid.startswith(_BTN_PREFIX):
                return
            data = getattr(inter, "data", None)
            if isinstance(data, dict):
                act = str(data.get("act") or "")
                arg_raw = data.get("arg")
                arg = str(arg_raw) if arg_raw is not None else None
            elif isinstance(data, str) and data.strip():
                parts = data.split()
                act, arg = parts[0], (parts[1] if len(parts) > 1 else None)
            else:
                return
            if not act:
                return
            logger.info("[肥鱼养成记] 按钮点击 aid=%r act=%r arg=%r", aid, act, arg)
            async for res in self._dispatch(event, act, arg):
                yield res

    # ------------------------------------------------------------ 文字指令
    @_make_yangyu_decorator()
    async def cmd_yangyu(self, event: AstrMessageEvent):
        async for res in self._yangyu(event):
            yield res
