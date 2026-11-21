"""AstrBot 插件壳：《肥鱼养成记》。协议转换层，玩法逻辑全在 game/ 里。

发送模式（与 zhutian/实测可用实现对齐）：
- 「md 文本卡 + 指令按钮」：Plain(_md(text)) + ActionRow(Button...) +
  use_markdown(True)；_md 用硬换行保留文字分行
- 指令按钮：对 qqofficial 适配器 _parse_keyboard 挂补丁，把回调按钮改写为
  QQ action.type=2——点击后由客户端以用户身份发送指令文本（群里可见，
  与原版一致），不再触发其它插件的 catch-all 回调
- 贴图/鱼图直接发原图，不做背景合成；按钮交互事件一律不发图
- 名号：优先采用平台昵称，未收到时引导，直接回复昵称文本即可登记（_pending +
  ALL 消息监听器），也可用「养鱼 昵称 名字」/「养鱼 跳过」
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import re
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path
from weakref import WeakValueDictionary

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context, Star, register

PLUGIN_DIR = Path(__file__).resolve().parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

# 文件完整性自检：混入旧版文件（半更新）时立刻报错并给出修复命令，
# 不允许"静默半残"运行。
import inspect as _inspect  # noqa: E402

from game import COMMANDS, GameConfig, JsonStore, Pond, load_or_create  # noqa: E402
from game.decorations import completed
from render import render_card_png, render_fish_png, render_markdown, render_plain  # noqa: E402
from render import stickers as sticker_src  # noqa: E402
from render.fishimg import render_memorial_png

if "scene" not in _inspect.signature(render_card_png).parameters:
    raise RuntimeError(
        "[肥鱼养成记] 插件文件不完整（检测到旧版 render/fishimg.py）。"
        "请备份存档后，从仓库下载完整插件文件并覆盖安装，随后重载插件。"
        "请确认 main.py、game/ 和 render/ 来自同一版本。"
    )

# ---------------------------------------------------------------- 版本兼容层
_button_dec = None
try:
    from astrbot.api.event.filter import button_action as _button_dec  # noqa: E402
except ImportError:
    pass

_command_dec = None
_emt_dec = None
_EMT = None
try:
    from astrbot.api.event.filter import EventMessageType as _EMT  # noqa: E402
    from astrbot.api.event.filter import event_message_type as _emt_dec
except ImportError:
    pass
_command_dec = None
try:
    from astrbot.api.event.filter import command as _command_dec  # noqa: E402
except ImportError:
    try:
        from astrbot.api.event import filter as _v3_filter  # noqa: E402

        _command_dec = _v3_filter.command
    except ImportError:
        pass

Plain = Image = ActionRow = Button = CallbackAction = None
try:
    from astrbot.api.message_components import ActionRow, Button, Image, Plain  # noqa: E402
except ImportError:
    try:
        from astrbot.api.message_components import Image, Plain  # noqa: E402
    except ImportError:
        pass
if Button is not None:
    try:
        from astrbot.api.message_components import CallbackAction as _CA  # noqa: E402

        CallbackAction = _CA
    except ImportError:
        CallbackAction = getattr(Button, "CallbackAction", None)
_HAS_BUTTONS = all(c is not None for c in (ActionRow, Button, CallbackAction))

# ---------------------------------------------------------------- 常量
_ACTION_KEYS = tuple(sorted((key for key in COMMANDS if key != "养鱼"), key=len, reverse=True))
_BASE_CMDS = set(COMMANDS) | {"养鱼"}
_SAFE_BARE_ALIASES = {"投喂", "加餐", "逗鱼", "净水", "玩耍", "称重", "出栏", "再养一条"}
_ALIAS_NAMES = _SAFE_BARE_ALIASES | {
    f"{prefix}{key}" for key in _BASE_CMDS for prefix in ("/", "#")
}
_ACTION_RE = re.compile("(" + "|".join(re.escape(key) for key in _ACTION_KEYS) + ")")
_NICK_ARG_RE = re.compile("(?:昵称|改名)\\s*(.+)$")
_SHELL_RE = re.compile("玩耍\\s*([123①②③])")
_BTN_PREFIX = "dfy:"
_NICK_TTL = 180.0  # 登记引导的有效期（秒），过期重新引导
_NICK_CLEAN_INTERVAL = 60.0
_NICK_MAX = 12
_POND_CACHE_MAX = 200
_CHAT_REWARD_NAMES = ("大肥鱼", "小鲸鱼", "小肥鱼", "鲸鱼", "肥鱼")

_VERSION_MATCH = re.search(
    r"(?m)^version:\s*[\"']?([^\"'\s]+)", (PLUGIN_DIR / "metadata.yaml").read_text(encoding="utf-8")
)
if not _VERSION_MATCH:
    raise RuntimeError("[肥鱼养成记] metadata.yaml 缺少 version")
_PLUGIN_VERSION = _VERSION_MATCH.group(1)


def _mentions_bot(event) -> bool:
    message = getattr(event, "message_obj", None)
    bot_id = str(getattr(message, "self_id", "") or "")
    getter = getattr(event, "get_self_id", None)
    if not bot_id and getter:
        bot_id = str(getter() or "")
    get_messages = getattr(event, "get_messages", None)
    components = get_messages() if get_messages else getattr(message, "message", ())
    for component in components or ():
        data = getattr(component, "data", None)
        kind = str(getattr(component, "type", "") or type(component).__name__).lower()
        target = getattr(component, "qq", None) or (
            data.get("qq") if isinstance(data, dict) else None
        )
        if "at" in kind and target is not None and str(target) == bot_id and bot_id:
            return True
    raw = getattr(message, "raw_message", None)
    if isinstance(raw, dict):
        raw_data = raw.get("raw_data") or raw
        for item in raw_data.get("message", ()) if isinstance(raw_data, dict) else ():
            if isinstance(item, dict) and item.get("type") == "at":
                target = (item.get("data") or {}).get("qq")
                if bot_id and str(target) == bot_id:
                    return True
    return False


def _sender_name(event) -> str:
    """读取 QQ 新昵称字段，兼容 SDK 对象和保留的原始字典。"""

    def field(value, key):
        return value.get(key) if isinstance(value, dict) else getattr(value, key, None)

    raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
    payload = field(raw, "raw_data")
    authors = (field(payload, "author"), field(raw, "author"))
    # SDK 可能丢弃新字段，优先查保留下来的原始载荷；只读发送者，不读 mentions。
    for key in ("member_nickname", "username"):
        for author in authors:
            name = field(author, key)
            if isinstance(name, str) and name.strip():
                return name.strip()
    return (event.get_sender_name() or "").strip()


_DIFF_SCALE = {"休闲": 0.7, "标准": 1.0, "硬核": 1.4}
_BTN_TIMEOUT = 86400  # 指令按钮无过期需求，给足 24h；fallback_text 兜底
_LABEL_MAX = 16
_MAX_BTN_ROWS = 5  # QQ 内联键盘最多 5 行
_MAX_BTN_PER_ROW = 5  # QQ 内联键盘每行最多 5 个

# 免费文本回昵称时，跳过这些指令开头（避免把指令当昵称吞掉）
_CMD_HEADS = tuple(
    sorted({f"{p}{k}" for k in _BASE_CMDS for p in ("", "/", "#")}, key=len, reverse=True)
)


def md_text(text: str) -> str:
    """用 Markdown 硬换行保留分行，不额外插入空段落。"""
    return "  \n".join(line.strip() for line in str(text or "").splitlines() if line.strip())


def _default_data_dir() -> Path:
    try:
        from astrbot.api.star import StarTools

        p = StarTools.get_data_dir("astrbot_plugin_dafeiyu")
        if isinstance(p, (str, Path)):
            return Path(p)
    except Exception:
        pass
    return PLUGIN_DIR.parent.parent / "plugin_data" / "astrbot_plugin_dafeiyu"


def _build_keyboard_locally(rows_components) -> dict:
    """不经 encode_button_callback（其 SQLite 回调注册表在并发写时会发生
    database is locked）直接构建 QQ 键盘 payload：我们的按钮全部是指令
    按钮 type=2，data 即指令文本，无需注册表。"""
    style_cls = getattr(Button, "Style", None)
    style_map = {}
    if style_cls is not None:
        style_map = {
            getattr(style_cls, n, None): v
            for n, v in (("DEFAULT", 0), ("PRIMARY", 3), ("SUCCESS", 1), ("DANGER", 0))
        }
        style_map.pop(None, None)
    out_rows = []
    for row in rows_components:
        buttons = []
        for btn in row.buttons:
            data = getattr(btn.action, "data", None) or {}
            cmd = str(data.get("cmd") or "")
            label = str(btn.label)[:16]
            buttons.append(
                {
                    "id": str(btn.id)[:64],
                    "render_data": {
                        "label": label,
                        "visited_label": label,
                        "style": style_map.get(btn.style, 0),
                    },
                    "action": {
                        "type": 2,
                        "permission": {"type": 2},
                        "data": cmd,
                        "enter": True,
                        "unsupport_tips": f"请直接发送：{cmd}",
                    },
                }
            )
        out_rows.append({"buttons": buttons})
    return {"content": {"rows": out_rows}}


def _install_command_button_patch() -> bool:
    """指令按钮方案：消息链里全部是「我们家的指令按钮」（data 含 cmd）时，
    本地直接构建 QQ 键盘 payload（type=2，不写回调注册表数据库，规避
    database is locked）；链里混有别家回调按钮时走原逻辑（适配器编码 token）。"""
    try:
        from astrbot.core.platform.sources.qqofficial.qqofficial_message_event import (
            QQOfficialMessageEvent as Target,
        )
    except Exception:
        return False
    if getattr(Target, "_dfy_cmd_patched", False):
        return True
    original = Target._parse_keyboard

    def shim(message_chain):
        rows = [c for c in message_chain.chain if isinstance(c, ActionRow) and c.buttons]
        all_buttons = [b for r in rows for b in r.buttons]
        ours = bool(all_buttons) and all(
            isinstance(getattr(b.action, "data", None), dict) and b.action.data.get("cmd")
            for b in all_buttons
        )
        if ours:
            try:
                payload = _build_keyboard_locally(rows)
                logger.debug(f"[dafeiyu-shim] 本地构建指令键盘：{len(all_buttons)} 按钮")
                return payload
            except Exception as e:
                logger.warning(f"[dafeiyu-shim] 本地构建失败({e})，回退原逻辑")
        return original(message_chain)

    try:
        Target._parse_keyboard = staticmethod(shim)
        Target._dfy_cmd_patched = True
        return True
    except Exception:
        return False


_patch_ok = _install_command_button_patch()
logger.info(
    "[肥鱼养成记] 模块导入完成：按钮组件=%s 指令装饰器=%s 按钮装饰器=%s 昵称监听=%s",
    _HAS_BUTTONS,
    _command_dec is not None,
    _button_dec is not None,
    _emt_dec is not None,
)
logger.info(
    "[肥鱼养成记] 指令按钮补丁（点按钮=发指令）：%s",
    "已安装" if _patch_ok else "不可用（退回回调交互按钮）",
)


def _make_yangyu_decorator():
    """「养鱼」主指令装饰器；兼容 v3/v4 的 alias 参数差异。"""
    if _command_dec is None:
        logger.warning("[肥鱼养成记] 未找到指令装饰器，文字指令不可用（仅按钮回调可用）")
        return lambda f: f
    try:
        return _command_dec("养鱼", alias=_ALIAS_NAMES)
    except TypeError:
        return _command_dec("养鱼")


@register(
    "astrbot_plugin_dafeiyu",
    "wuhen007",
    "肥鱼养成记：全群共养一条大肥鱼，养成出栏结算",
    _PLUGIN_VERSION,
)
class DaFeiYuPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        self.context = context
        raw = dict(config) if config is not None else {}
        try:
            if isinstance(raw.get("astrbot_plugin_dafeiyu"), dict):
                raw = raw["astrbot_plugin_dafeiyu"]
        except Exception:
            pass
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
        self._custom_img_dir = data_dir / "fish_imgs"  # 手动贴图（优先级最高）
        self._sticker_enabled = raw.get("sticker_enabled", True)
        self._sticker_api = raw.get("sticker_api", "https://fisharchive.cc/stickers/index.json")
        self._sticker_dir = data_dir / "stickers"
        self._sticker_warned = False
        self._ponds = OrderedDict()
        self._pond_cache_lock = threading.Lock()
        self._locks = WeakValueDictionary()
        self._pending = {}  # (session, uid) -> 登记引导截止时刻
        self._pending_cleanup_at = 0.0
        logger.info(
            "[肥鱼养成记] 已加载：输出=%s 画图=%s 贴图=%s 难度=%s 存档=%s",
            "markdown" if self._md_mode else "text",
            self._send_image,
            self._sticker_enabled,
            raw.get("difficulty", "标准"),
            data_dir,
        )

    async def terminate(self):
        pass

    # ------------------------------------------------------------ 内部
    def _get_pond(self, event) -> Pond:
        session = event.unified_msg_origin
        with self._pond_cache_lock:
            pond = self._ponds.get(session)
            if pond is None:
                pond = load_or_create(
                    self._store, session, config=self._cfg, clock=time.time, rng=random.Random()
                )
                self._ponds[session] = pond
                if len(self._ponds) > _POND_CACHE_MAX:
                    self._ponds.popitem(last=False)
            else:
                self._ponds.move_to_end(session)
            return pond

    def _pick_custom_img(self):
        """fish_imgs/ 手动贴图随机一张（优先级最高）；没有则 None。"""
        imgs = []
        try:
            if self._custom_img_dir.is_dir():
                imgs = [
                    p
                    for p in self._custom_img_dir.iterdir()
                    if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp")
                ]
            if imgs:
                # fish_imgs 里可能混有 webp：统一转 PNG 再发（QQ 富媒体不支持 webp）
                return sticker_src.ensure_png(str(random.choice(imgs)), self._sticker_dir / "img")
        except Exception as e:
            logger.warning(f"[肥鱼养成记] 自定义鱼图读取失败：{e}")
        return None

    _STICKER_MAP = {
        "feed": (["吃饭"], ["吃", "饭", "炫", "饿"]),
        "snack": (["吃饭", "开心"], ["吃", "零食", "海苔"]),
        "pet": (["开心", "卖萌"], ["开心", "笑", "蹭"]),
        "clean": (["运动"], ["水", "洗", "干净"]),
        "play_open": (["搞笑", "卖萌"], ["玩", "圈", "泡泡"]),
        "play": (["得意", "开心"], ["精准", "战利品", "圈"]),
        "weigh": (["得意", "震惊"], ["胖", "体重", "称"]),
        "cooldown": (["睡觉", "敷衍"], ["休息", "累", "困"]),
        "graduate": (["开心", "得意"], ["开心", "庆祝", "毕业"]),
        "runaway": (["难过", "委屈"], ["走", "离开", "哭"]),
        "need_nick": (["卖萌"], ["你好", "报到", "初次"]),
        "panel": (["开心", "得意"], ["开心", "游"]),
        "sign": (["开心", "卖萌"], ["打卡", "签到"]),
        "fishing": (["搞笑", "摸鱼"], ["钓", "鱼竿"]),
        "rescue": (["震惊", "无语"], ["救", "缠"]),
        "wish": (["卖萌"], ["愿", "星"]),
    }
    _STICKER_TITLE_KEY = {
        "投喂": "feed",
        "加餐": "snack",
        "逗鱼": "pet",
        "净水": "clean",
        "称重": "weigh",
        "稍等一下": "cooldown",
        "套圈小游戏": "play_open",
        "套圈": "play",
        "名号登记": "pet",
        "每日打卡": "sign",
        "钓鱼": "fishing",
        "求援中": "rescue",
        "救援成功": "sign",
        "许愿池": "wish",
    }

    def _sticker_key(self, result) -> str:
        if result.event in ("panel", "graduated", "runaway", "need_nick"):
            return result.event
        return self._STICKER_TITLE_KEY.get(result.title, "panel")

    def _get_sticker(self, result, pond: Pond):
        """按指令/状态从贴图站挑一张；优先复用本地已缓存的贴图（减轻素材站
        压力），挑选种子与状态绑定：同一状态稳定命中同一张。"""
        if not self._sticker_enabled:
            return None
        try:
            key = self._sticker_key(result)
            tags, kws = self._STICKER_MAP.get(key, self._STICKER_MAP["panel"])
            stickers = sticker_src.load_index(self._sticker_dir, self._sticker_api)
            if not stickers:
                return None
            fish = pond.state.fish
            seed = f"{key}|{round(fish.satiety)}|{round(fish.mood)}|{round(fish.weight)}"
            manifest = sticker_src.load_manifest(self._sticker_dir)
            s = sticker_src.pick_sticker(
                stickers, tags, kws, random.Random(seed), cached_urls=set(manifest)
            )
            if not s:
                return None
            return sticker_src.download_sticker(s, self._sticker_dir / "img", manifest=manifest)
        except Exception as e:
            if not self._sticker_warned:
                self._sticker_warned = True
                logger.warning(f"[肥鱼养成记] 贴图源不可用，回退内置鱼图：{e}")
            return None

    def _fallback_fish(self, session: str, pond: Pond) -> str:
        h = hashlib.md5(session.encode("utf-8")).hexdigest()[:10]
        return render_fish_png(pond.state, str(self._image_dir / f"{h}.png"), config=pond.cfg)

    def _md(self, text: str) -> str:
        return md_text(text)

    def _build_rows(self, result):
        """引擎按钮 → ActionRow。data 带整条指令 {"cmd": "/养鱼 投喂"}，
        适配器补丁改写为指令按钮；无按钮组件返回空。"""
        if not _HAS_BUTTONS:
            return []
        style_cls = getattr(Button, "Style", None)
        style_default = getattr(style_cls, "DEFAULT", None) if style_cls else None
        items = []
        for b in result.buttons:
            parts = b.command.split()
            if parts and parts[0] == "养鱼":
                rest = parts[1:]
            else:
                rest = parts
            cmd = "/养鱼" + (" " + " ".join(rest) if rest else "")
            act = rest[0] if rest else "养鱼"
            arg = rest[1] if len(rest) > 1 else None
            label = str(b.label)[:_LABEL_MAX]
            ident = f"{_BTN_PREFIX}{act}|{arg or ''}|{label}"[:64]
            items.append((ident, label, {"cmd": cmd, "act": act, "arg": arg}))
        rows = []
        # QQ 官方内联键盘上限：最多 5 行、每行最多 5 个按钮（超了报 40034029
        # 「内联键盘行/列超限」）。按钮少时保持每行 3 个更好点；按钮多时自动
        # 加宽到 4~5 个，保证总行数不越过 5 行。
        if items:
            per_row = min(_MAX_BTN_PER_ROW, max(3, -(-len(items) // _MAX_BTN_ROWS)))
            chunks = [items[i : i + per_row] for i in range(0, len(items), per_row)]
            if len(chunks) > _MAX_BTN_ROWS:
                kept = chunks[:_MAX_BTN_ROWS]
                dropped = [i for chunk in chunks[_MAX_BTN_ROWS:] for i in chunk]
                chunks = kept
                logger.warning(
                    f"[dafeiyu] 按钮 {len(items)} 个超过 {_MAX_BTN_ROWS} 行上限，"
                    f"已省略 {len(dropped)} 个：{[d[1] for d in dropped]}"
                )
        else:
            chunks = []
        for chunk in chunks:
            buttons = []
            for ident, label, data in chunk:
                kw = {"id": ident, "label": label, "action": CallbackAction(data=data)}
                if style_default is not None:
                    kw["style"] = style_default
                buttons.append(Button(**kw))
            rows.append(
                ActionRow(
                    buttons=buttons,
                    fallback_text="按钮不可用，请发送对应指令：\n"
                    + "\n".join(
                        f"{label}：{data['cmd']}" for _, label, data in chunk
                    ),
                    action_timeout=_BTN_TIMEOUT,
                )
            )
        return rows

    def _build_result(self, event, result):
        """「多行文本卡 + 指令按钮」：markdown 模式且有按钮组件走 msg_type=2
        （_md 保留硬换行，不插入空段落）；否则纯文本全量卡片（\\n 原生换行）。
        有真按钮时不再输出 [按钮] 提示行（避免重复暴露）。"""
        # dafeiyu_suite 的 single_line_reply 读取此标记后保留换行。
        set_extra = getattr(event, "set_extra", None)
        if set_extra:
            set_extra("reply_keep_format", True)
        rows = self._build_rows(result) if self._md_mode else []
        if rows:
            lines = render_markdown(result).splitlines()
            if result.buttons and lines:
                lines.pop()
            text = "\n".join(lines)
            res = event.chain_result([Plain(self._md(text)), *rows])
            try:
                res.use_markdown(True)
            except Exception:
                pass
            return res
        return event.plain_result(render_plain(result))

    async def _dispatch(self, event, command: str, arg=None):
        try:
            uid = str(event.get_sender_id() or "anon")
            name = _sender_name(event)  # 未收到昵称时引导登记名号
            session = event.unified_msg_origin
            lock = self._locks.setdefault(session, asyncio.Lock())
            async with lock:
                pond = await asyncio.to_thread(self._get_pond, event)
                result = await asyncio.to_thread(pond.handle, uid, name, command, arg)
            if result.event == "need_nick":
                # 引导免费文本回昵称（180s 内的下一条非指令消息即登记）
                now = time.time()
                if now >= self._pending_cleanup_at:
                    self._pending = {
                        key: deadline for key, deadline in self._pending.items() if deadline > now
                    }
                    self._pending_cleanup_at = now + _NICK_CLEAN_INTERVAL
                self._pending[(session, uid)] = now + _NICK_TTL
                try:
                    event.stop_event()
                except Exception:
                    pass
            elif pond.state.users.get(uid) and pond.state.users[uid].nick_set:
                self._pending.pop((session, uid), None)
            # ① 贴图原图直发（不合成背景）；交互事件不发图
            if self._send_image and Image is not None and event.get_button_interaction() is None:
                try:
                    image_key = hashlib.md5(session.encode("utf-8")).hexdigest()[:10]
                    if result.data.get("memorial"):
                        custom = await asyncio.to_thread(
                            render_memorial_png,
                            result.data["memorial"],
                            str(self._image_dir / f"{image_key}-memorial.png"),
                            pond.cfg,
                        )
                    else:
                        custom = await asyncio.to_thread(self._get_sticker, result, pond)
                        if custom is None:
                            custom = await asyncio.to_thread(self._pick_custom_img)
                        if completed(pond.state.decorations):
                            custom = await asyncio.to_thread(
                                render_card_png,
                                pond.state,
                                ["大家共建的鱼塘：" + "、".join(completed(pond.state.decorations))],
                                str(self._image_dir / f"{image_key}-decor.png"),
                                pond.cfg,
                                custom,
                            )
                        elif custom is None:
                            custom = await asyncio.to_thread(self._fallback_fish, session, pond)
                    if custom:
                        yield event.image_result(custom)
                except Exception as e:
                    logger.warning(f"[肥鱼养成记] 发图失败，跳过：{e}")
            # ② 文字卡片和原生按钮。
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
            if sub in ("昵称", "改名"):
                m2 = _NICK_ARG_RE.search(text)
                arg = m2.group(1).strip() if m2 else None
            elif sub in (
                "猜鱼",
                "寻宝",
                "奇遇",
                "游历",
                "建设",
                "装修",
                "纪念册",
                "合影",
                "运动会",
                "性格",
                "委托",
                "周报",
            ):
                arg = text[act.end() :].strip() or None
        async for res in self._dispatch(event, sub, arg):
            yield res

    # ------------------------------------------------------------ 名号登记（免费文本）
    if _emt_dec is not None and _EMT is not None:

        @_emt_dec(_EMT.ALL, priority=101)
        async def on_chat_reward(self, event: AstrMessageEvent):
            try:
                session = event.unified_msg_origin
                if "GroupMessage" not in str(session):
                    return
                uid = str(event.get_sender_id() or "")
                if not uid:
                    return
                text = (event.message_str or "").strip()
                mentioned = _mentions_bot(event)
                if (
                    (not text and not mentioned)
                    or text.startswith(_CMD_HEADS)
                    or event.get_button_interaction() is not None
                ):
                    return
                if not any(name in text for name in _CHAT_REWARD_NAMES) and not mentioned:
                    return
                lock = self._locks.setdefault(session, asyncio.Lock())
                async with lock:
                    pond = await asyncio.to_thread(self._get_pond, event)
                    reward = await asyncio.to_thread(pond.chat_reward, uid, _sender_name(event))
                if reward:
                    item = "白饭" if reward == "rice" else "Token"
                    yield event.plain_result(f"和大肥鱼聊得开心，获得了 {item} ×1！")
            except Exception:
                logger.exception("[肥鱼养成记] 群聊奖励处理失败")

        @_emt_dec(_EMT.ALL, priority=100)
        async def on_nickname_reply(self, event: AstrMessageEvent):
            try:
                cid = event.unified_msg_origin
                uid = str(event.get_sender_id() or "")
                deadline = self._pending.get((cid, uid))
                if not deadline:
                    return
                if time.time() > deadline:
                    self._pending.pop((cid, uid), None)
                    return
                text = (event.message_str or "").strip()
                if not text or text.startswith(_CMD_HEADS):
                    return  # 指令不吞，走正常指令管线
                self._pending.pop((cid, uid), None)
                try:
                    event.stop_event()
                except Exception:
                    pass
                nick = text[:_NICK_MAX]
                async for res in self._dispatch(event, "昵称", nick):
                    yield res
            except Exception as e:
                logger.exception("[肥鱼养成记] 名号登记失败")
                try:
                    yield event.plain_result(f"[肥鱼养成记] 内部错误：{type(e).__name__}: {e}")
                except Exception:
                    pass

    # ------------------------------------------------------------ 按钮回调（兜底：补丁未生效时走交互）
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
            # zhutian 等插件也注册了全局按钮回调，先停住事件别让它插话
            try:
                event.stop_event()
            except Exception:
                pass
            data = getattr(inter, "data", None)
            act = arg = None
            if isinstance(data, dict) and data.get("act"):
                act = str(data["act"])
                arg_raw = data.get("arg")
                arg = str(arg_raw) if arg_raw is not None else None
            elif isinstance(data, dict) and data.get("cmd"):
                parts = str(data["cmd"]).lstrip("/").split()
                if parts and parts[0] == "养鱼":
                    parts = parts[1:]
                if parts:
                    act, arg = parts[0], (parts[1] if len(parts) > 1 else None)
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
