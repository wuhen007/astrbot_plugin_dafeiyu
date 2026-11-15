"""AstrBot 插件入口：《肥鱼养成记》。

- 指令：养鱼（别名：投喂/加餐/逗鱼/净水/玩耍/称重/排行/档案/出栏/再养一条/帮助）
- 输出：Markdown 卡片 + 实时鱼图 + 原生按钮（Button/ActionRow，AstrBot v4+）；
  旧版无按钮组件时自动降级为文字指令（卡片文案自带 [按钮] 提示行）。
- 按钮点击经 ButtonInteraction 事件回调，路由到同一套引擎逻辑。
- 并发：同一会话用 asyncio.Lock 串行；存档经 JsonStore 每次操作后自动落盘。
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
from render import render_fish_png, render_markdown, render_plain         # noqa: E402

# ---------------------------------------------------------------- 版本兼容层
# v4：astrbot.api.event.filter 包提供 command / button_action 独立装饰器
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
    from astrbot.api.message_components import (                          # noqa: E402
        ActionRow, Button, CallbackAction, Image, Plain,
    )
except ImportError:
    try:
        from astrbot.api.message_components import Image, Plain           # noqa: E402
    except ImportError:
        pass
_HAS_BUTTONS = all(c is not None for c in (ActionRow, Button, CallbackAction))

# ---------------------------------------------------------------- 常量
_ACTION_KEYS = ("投喂", "加餐", "逗鱼", "净水", "玩耍", "称重", "排行", "排行榜",
                "档案", "图鉴", "出栏", "再养一条", "帮助")
_ACTION_RE = re.compile("(投喂|加餐|逗鱼|净水|玩耍|称重|排行|排行榜|档案|图鉴|出栏|再养一条|帮助)")
_SHELL_RE = re.compile("玩耍\\s*([123①②③])")
_BTN_PREFIX = "dfy:"
_DIFF_SCALE = {"休闲": 0.7, "标准": 1.0, "硬核": 1.4}

logger.info("[肥鱼养成记] 模块导入完成：按钮组件=%s 指令装饰器=%s 按钮装饰器=%s",
            _HAS_BUTTONS, _command_dec is not None, _button_dec is not None)


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
        return _command_dec("养鱼", alias=set(_ACTION_KEYS))
    except TypeError:
        return _command_dec("养鱼")


@register("astrbot_plugin_dafeiyu", "wuhen007",
          "肥鱼养成记：全群共养一条大肥鱼，养成出栏结算", "v1.0.0")
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

    def _build_rows(self, result):
        """把引擎按钮转成 ActionRow（每行 3 个）；不支持按钮的版本返回空。"""
        if not _HAS_BUTTONS:
            return []
        payloads = []
        for b in result.buttons:
            parts = b.command.split()
            if parts and parts[0] == "养鱼":
                parts = parts[1:]
            data = " ".join(parts) if parts else "养鱼"
            payloads.append((b.label, data))
        rows = []
        for i in range(0, len(payloads), 3):
            chunk = payloads[i:i + 3]
            buttons = [Button(id=f"{_BTN_PREFIX}{data}", label=label,
                              action=CallbackAction(data=data))
                       for label, data in chunk]
            rows.append(ActionRow(buttons=buttons,
                                  fallback_text=" ".join(f"[{l}]" for l, _ in chunk)))
        return rows

    def _build_result(self, event, pond, result):
        text = render_markdown(result) if self._md_mode else render_plain(result)
        chain = []
        if self._send_image and Image is not None:
            try:
                h = hashlib.md5(event.unified_msg_origin.encode("utf-8")).hexdigest()[:10]
                img_path = render_fish_png(pond.state,
                                           str(self._image_dir / f"{h}.png"),
                                           config=pond.cfg)
                chain.append(Image.fromFileSystem(img_path))
            except Exception as e:
                logger.warning(f"[肥鱼养成记] 画鱼失败，跳过：{e}")
        chain.append(Plain(text))
        chain.extend(self._build_rows(result))
        res = event.chain_result(chain)
        try:
            res.use_markdown(True if self._md_mode else False)
        except Exception:
            pass    # 旧版 MessageChain 无 use_markdown，跟随平台默认
        return res

    async def _dispatch(self, event, command: str, arg=None):
        try:
            uid = str(event.get_sender_id() or "anon")
            name = (event.get_sender_name() or "").strip() or "鱼友"
            session = event.unified_msg_origin
            lock = self._locks.setdefault(session, asyncio.Lock())
            async with lock:
                pond = self._get_pond(event)
                result = pond.handle(uid, name, command, arg)
            yield self._build_result(event, pond, result)
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
            data = inter.data if isinstance(inter.data, str) else ""
            logger.info("[肥鱼养成记] 按钮点击 aid=%r data=%r", aid, data)
            parts = data.split()
            if not parts:
                return
            arg = parts[1] if len(parts) > 1 else None
            async for res in self._dispatch(event, parts[0], arg):
                yield res

    # ------------------------------------------------------------ 文字指令
    @_make_yangyu_decorator()
    async def cmd_yangyu(self, event: AstrMessageEvent):
        async for res in self._yangyu(event):
            yield res
