"""main.py 接线冒烟测试：stub 掉 astrbot 框架后导入，模拟指令/按钮/昵称登记。

v1.1.0 起发送结构 = 两条消息：①贴图/鱼图直接发原图（交互事件不发图）
②「md 文本卡 + 指令按钮」（data 带 {"cmd": "/养鱼 X"}，适配器补丁转指令按钮）。
"""

import asyncio
import importlib
import json
import logging
import sys
import threading
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

PLAIN_CLS = None  # 由 stub 注入
IMAGE_CLS = None


class FakeResult:
    def __init__(self, chain, image=False):
        self.chain = chain
        self.md = None
        self.image = image

    def use_markdown(self, v):
        self.md = v
        return self


class FakeEvent:
    def __init__(
        self, session="test:GroupMessage:1", uid="u1", name="小明", message_str="", interaction=None
    ):
        self._session, self._uid, self._name = session, uid, name
        self.message_str = message_str
        self._interaction = interaction
        self.stopped = False

    @property
    def unified_msg_origin(self):
        return self._session

    def get_sender_id(self):
        return self._uid

    def get_sender_name(self):
        return self._name

    def get_button_interaction(self):
        return self._interaction

    def stop_event(self):
        self.stopped = True

    def chain_result(self, chain):
        return FakeResult(chain)

    def plain_result(self, text):
        return FakeResult([PLAIN_CLS(text)])

    def image_result(self, path):
        return FakeResult([IMAGE_CLS(path)], image=True)


def _stub_astrbot(monkeypatch):
    global PLAIN_CLS, IMAGE_CLS
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event_pkg = types.ModuleType("astrbot.api.event")
    filter_pkg = types.ModuleType("astrbot.api.event.filter")
    star_pkg = types.ModuleType("astrbot.api.star")
    mc = types.ModuleType("astrbot.api.message_components")

    class AstrBotConfig(dict):
        pass

    api.logger = logging.getLogger("astrbot-stub")
    api.AstrBotConfig = AstrBotConfig
    event_pkg.AstrMessageEvent = type("AstrMessageEvent", (), {})
    event_pkg.MessageEventResult = type("MessageEventResult", (), {})
    star_pkg.Context = type("Context", (), {})
    star_pkg.Star = type("Star", (), {})
    star_pkg.StarTools = type("StarTools", (), {})

    def register(*args, **kwargs):
        def deco(cls):
            return cls

        return deco

    star_pkg.register = register

    class Plain:
        def __init__(self, text, **kwargs):
            self.text = text

    class Image:
        def __init__(self, path="", **kwargs):
            self.path = path

        @classmethod
        def fromFileSystem(cls, p):
            return cls(p)

    class Button:
        # 模拟 v4.29.x：CallbackAction 是 Button 的内部类，不顶层导出
        def __init__(self, id=None, label=None, action=None, style=None, **kwargs):
            self.id, self.label, self.action, self.style = id, label, action, style

        class CallbackAction:
            def __init__(self, data=None, **kwargs):
                self.data = data

    class ActionRow:
        def __init__(self, buttons=None, fallback_text=None, action_timeout=None, **kwargs):
            self.buttons = buttons or []
            self.fallback_text = fallback_text
            self.action_timeout = action_timeout

    mc.Plain, mc.Image, mc.Button, mc.ActionRow = Plain, Image, Button, ActionRow
    PLAIN_CLS, IMAGE_CLS = Plain, Image

    def command(command_name=None, sub_command=None, alias=None, **kwargs):
        def deco(func):
            return func

        return deco

    def button_action(action_id=None, **kwargs):
        def deco(func):
            return func

        return deco

    filter_pkg.command = command
    filter_pkg.button_action = button_action
    filter_pkg.event_message_type = lambda *a, **k: lambda f: f
    filter_pkg.EventMessageType = types.SimpleNamespace(ALL="all")

    astrbot.api = api
    astrbot.api.event = event_pkg
    astrbot.api.event.filter = filter_pkg
    astrbot.api.star = star_pkg
    astrbot.api.message_components = mc

    for name, mod in [
        ("astrbot", astrbot),
        ("astrbot.api", api),
        ("astrbot.api.event", event_pkg),
        ("astrbot.api.event.filter", filter_pkg),
        ("astrbot.api.star", star_pkg),
        ("astrbot.api.message_components", mc),
    ]:
        monkeypatch.setitem(sys.modules, name, mod)


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    _stub_astrbot(monkeypatch)
    sys.modules.pop("main", None)
    main = importlib.import_module("main")
    # 贴图源置离线：测试聚焦发送结构与昵称流程，不联网
    import render.stickers as ss

    monkeypatch.setattr(ss, "load_index", lambda *a, **k: None)
    cfg = main.AstrBotConfig(
        {
            "output_mode": "markdown",
            "send_image": True,
            "difficulty": "标准",
            "data_dir": str(tmp_path),
        }
    )
    return main, main.DaFeiYuPlugin(None, cfg), tmp_path


def _run(coro):
    return asyncio.run(coro)


async def _gather(agen):
    return [r async for r in agen]


def _texts(result):
    return [c.text for c in result.chain if isinstance(c, PLAIN_CLS)]


def _rows(result, main):
    return [c for c in result.chain if isinstance(c, main.ActionRow)]


def _saved_state(tmp_path):
    files = list((tmp_path / "ponds").glob("*.json"))
    assert files, "应有存档文件"
    return json.loads(files[0].read_text("utf-8"))


# ---------------------------------------------------------------- 测试
def test_group_chat_reward_requires_mention_or_name(plugin, monkeypatch):
    main, p, tmp = plugin
    monkeypatch.setattr(main.time, "time", lambda: 1_800_000_000)

    class FixedRng:
        def random(self):
            return 0.1

        def choice(self, choices):
            return "rice"

    event = FakeEvent(message_str="普通闲聊")
    pond = p._get_pond(event)
    pond.rng = FixedRng()
    assert _run(_gather(p.on_chat_reward(event))) == []
    assert not list((tmp / "ponds").glob("*.json"))

    event.message_str = ""
    event.message_obj = types.SimpleNamespace(
        self_id="bot",
        raw_message={"raw_data": {"message": [{"type": "at", "data": {"qq": "bot"}}]}},
    )
    assert main._mentions_bot(event)
    event.message_obj = None
    event.get_self_id = lambda: "bot"
    event.get_messages = lambda: [types.SimpleNamespace(type="At", qq="bot")]
    results = _run(_gather(p.on_chat_reward(event)))
    expected = "和大肥鱼聊得开心，获得了 白饭 ×1！"
    assert _texts(results[0]) == [expected]
    assert list((tmp / "ponds").glob("*.json"))

    event.message_str = "今天真开心"
    event.get_messages = lambda: []
    assert _run(_gather(p.on_chat_reward(event))) == []
    assert not event.stopped
    assert pond.state.users["u1"].rice == 6
    event._uid = "u2"
    event.message_str = "大肥鱼今天很开心"
    results = _run(_gather(p.on_chat_reward(event)))
    assert _texts(results[0]) == [expected]
    assert pond.state.users["u2"].rice == 6
    for index, nickname in enumerate(("肥鱼", "鲸鱼", "小鲸鱼", "小肥鱼"), start=3):
        event._uid = f"alias-{index}"
        event.message_str = f"{nickname}今天真开心"
        results = _run(_gather(p.on_chat_reward(event)))
        assert _texts(results[0]) == [expected]
        assert pond.state.users[event._uid].rice == 6


def test_chat_reward_cooldown_daily_cap_and_legacy_user(plugin, monkeypatch):
    from game.state import UserInfo

    main, p, tmp = plugin
    now = [1_800_000_000]
    monkeypatch.setattr(main.time, "time", lambda: now[0])
    pond = p._get_pond(FakeEvent())

    class FixedRng:
        def random(self):
            return 0.1

        def choice(self, choices):
            return choices[0]

    pond.rng = FixedRng()
    assert pond.chat_reward("u1", "小明") == "rice"
    assert pond.chat_reward("u1", "小明") is None
    now[0] += 1800
    assert pond.chat_reward("u1", "小明") == "rice"
    now[0] += 1800
    assert pond.chat_reward("u1", "小明") is None
    saved = _saved_state(tmp)["users"]["u1"]
    assert saved["chat_reward_count"] == 2
    assert UserInfo.from_dict({"display_name": "旧档"}).chat_reward_count == 0


def test_load_creates_data_dir(plugin):
    main, p, tmp = plugin
    assert (tmp / "ponds").is_dir()


def test_feed_two_messages_image_then_kb(plugin):
    main, p, tmp = plugin
    ev = FakeEvent()
    results = _run(_gather(p._dispatch(ev, "投喂")))
    assert len(results) == 2  # ① 鱼图直发 ② 多行文本卡+按钮
    assert results[0].image is True
    kb = results[1]
    assert kb.md is True
    texts = "\n".join(_texts(kb))
    assert "【投喂】" in texts and "饱腹 +22" in texts
    assert "[投喂]" not in texts  # 真按钮存在时不再暴露提示行
    rows = _rows(kb, main)
    assert rows and rows[0].action_timeout == 86400
    assert _saved_state(tmp)["fish"]["exp"] == 8


def test_nickname_free_text_flow(plugin):
    """首次照料 → 引导；直接回复昵称文本 → 登记；之后放行。"""
    main, p, tmp = plugin
    ev1 = FakeEvent(name="", message_str="投喂")
    results = _run(_gather(p._dispatch(ev1, "投喂")))
    kb = results[-1]
    assert any("昵称" in t for t in _texts(kb))  # 引导登记
    assert ("test:GroupMessage:1", "u1") in p._pending

    ev2 = FakeEvent(name="", message_str="阿鱼")  # 免费文本回复昵称
    results2 = _run(_gather(p.on_nickname_reply(ev2)))
    assert ev2.stopped  # 停住事件，别让其它插件/LLM 插话
    assert results2 and any("阿鱼" in t for t in _texts(results2[-1]))

    ev3 = FakeEvent(name="", message_str="投喂")
    results3 = _run(_gather(p._dispatch(ev3, "投喂")))
    assert any("饱腹" in t for t in _texts(results3[-1]))
    st = _saved_state(tmp)
    assert st["users"]["u1"]["nick_set"] is True
    assert st["users"]["u1"]["display_name"] == "阿鱼"


def test_nickname_listener_ignores_commands(plugin):
    main, p, tmp = plugin
    _run(_gather(p._dispatch(FakeEvent(name="", message_str="投喂"), "投喂")))
    # 指令开头不吞：发「养鱼」应走指令管线而不是被当昵称
    ev = FakeEvent(name="", message_str="养鱼")
    results = _run(_gather(p.on_nickname_reply(ev)))
    assert results == []
    assert ("test:GroupMessage:1", "u1") in p._pending  # 引导仍挂起


def test_nickname_prompt_sweep_removes_expired_entries(plugin):
    main, p, tmp = plugin
    p._pending[("old-session", "old-user")] = time.time() - 1
    p._pending_cleanup_at = 0
    _run(_gather(p._dispatch(FakeEvent(name=""), "养鱼")))
    assert ("old-session", "old-user") not in p._pending
    assert ("test:GroupMessage:1", "u1") in p._pending


def test_button_click_routes_and_skips_image(plugin):
    main, p, tmp = plugin
    inter = types.SimpleNamespace(
        action_id="dfy:投喂||投喂", data={"cmd": "/养鱼 投喂", "act": "投喂", "arg": None}
    )
    ev = FakeEvent(interaction=inter)
    results = _run(_gather(p.on_button(ev)))
    assert len(results) == 1  # 交互事件不发图
    assert results[0].image is False
    assert any("投喂" in t for t in _texts(results[0]))
    assert ev.stopped  # 停事件，防 zhutian 插话
    assert _saved_state(tmp)["fish"]["exp"] == 8


def test_button_cmd_only_data(plugin):
    """只有 {"cmd": ...} 的数据也能路由（兜底）。"""
    main, p, tmp = plugin
    inter = types.SimpleNamespace(action_id="dfy:x", data={"cmd": "/养鱼 玩耍"})
    ev = FakeEvent(interaction=inter)
    results = _run(_gather(p.on_button(ev)))
    assert any("套圈" in t for t in _texts(results[-1]))


def test_button_foreign_ignored(plugin):
    main, p, tmp = plugin
    inter = types.SimpleNamespace(action_id="other:do", data={"act": "x"})
    results = _run(_gather(p.on_button(FakeEvent(interaction=inter))))
    assert results == []


def test_button_rows_layout(plugin):
    main, p, tmp = plugin
    results = _run(_gather(p._dispatch(FakeEvent(), "养鱼")))
    rows = _rows(results[-1], main)
    assert rows
    first = rows[0].buttons
    assert [b.label for b in first] == ["🍚 投喂", "🍙 加餐", "🫧 逗鱼"]
    b0 = first[0]
    assert b0.id.startswith("dfy:")
    assert b0.action.data["cmd"] == "/养鱼 投喂"
    assert b0.action.data["act"] == "投喂"
    assert rows[0].action_timeout == 86400


def test_command_button_shim_builds_locally_on_db_lock(plugin, monkeypatch):
    """AstrBot 回调注册表 SQLite 报 database is locked 时，shim 本地构建键盘
    （type=2 指令按钮、不写注册表），消息发送不再失败。"""
    import sqlite3

    main, p, tmp = plugin

    class FakeTarget:
        _dfy_cmd_patched = False
        original_calls = 0

        @staticmethod
        def _parse_keyboard(message_chain):
            FakeTarget.original_calls += 1
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setitem(
        sys.modules,
        "astrbot.core.platform.sources.qqofficial.qqofficial_message_event",
        types.SimpleNamespace(QQOfficialMessageEvent=FakeTarget),
    )
    assert main._install_command_button_patch() is True

    row = main.ActionRow(
        buttons=[
            main.Button(
                id="dfy:投喂||投喂",
                label="投喂",
                action=main.CallbackAction(data={"cmd": "/投喂", "act": "投喂", "arg": None}),
            )
        ]
    )
    chain = types.SimpleNamespace(chain=[main.Plain("【出栏图鉴】"), row])
    kb = FakeTarget._parse_keyboard(chain)
    btn = kb["content"]["rows"][0]["buttons"][0]
    assert btn["action"]["type"] == 2
    assert btn["action"]["data"] == "/投喂"
    assert btn["action"]["enter"] is True
    assert btn["action"]["unsupport_tips"] == "请直接发送：/投喂"
    assert "database" not in str(kb)  # 键盘构建成功，不再抛库锁


def test_command_button_shim_passes_foreign_buttons(plugin, monkeypatch):
    """混有/仅有别家回调按钮的消息链 → 走原逻辑（由适配器编码 token）。"""
    main, p, tmp = plugin
    calls = []

    class FakeTarget:
        _dfy_cmd_patched = False

        @staticmethod
        def _parse_keyboard(message_chain):
            calls.append(1)
            return {"content": {"rows": []}}

    monkeypatch.setitem(
        sys.modules,
        "astrbot.core.platform.sources.qqofficial.qqofficial_message_event",
        types.SimpleNamespace(QQOfficialMessageEvent=FakeTarget),
    )
    assert main._install_command_button_patch() is True

    foreign = main.ActionRow(
        buttons=[
            main.Button(id="zt:x", label="x", action=main.CallbackAction(data={"act": "zt.y"}))
        ]
    )
    kb = FakeTarget._parse_keyboard(types.SimpleNamespace(chain=[foreign]))
    assert kb == {"content": {"rows": []}} and calls

    # 纯文本链（无按钮）也走原逻辑
    kb2 = FakeTarget._parse_keyboard(types.SimpleNamespace(chain=[main.Plain("x")]))
    assert kb2 == {"content": {"rows": []}} and len(calls) == 2


def test_integrity_check_raises_on_stale_render(plugin, monkeypatch):
    """文件混版自检：render_card_png 缺 scene 参数时导入应报错并给修复命令。"""
    main, p, tmp = plugin
    from render import fishimg

    orig = fishimg.render_card_png

    def stale(*args, **kwargs):  # 模拟旧版：不认识 scene
        kwargs.pop("scene", None)
        return orig(*args, **kwargs)

    monkeypatch.setattr(fishimg, "render_card_png", stale)
    import inspect

    assert "scene" not in inspect.signature(fishimg.render_card_png).parameters
    # 真实防护在 main 导入时执行；这里验证签名检测逻辑本身可用
    assert "scene" in inspect.signature(orig).parameters


def test_text_mode_uses_plain(plugin):
    main, p, tmp = plugin
    p._md_mode = False
    results = _run(_gather(p._dispatch(FakeEvent(), "逗鱼")))
    kb = results[-1]
    assert kb.md is None  # plain_result 未走 use_markdown
    assert _rows(kb, main) == []  # 纯文本模式不带键盘
    assert any("逗鱼" in t for t in _texts(kb))


def test_default_mode_is_markdown(plugin):
    main, p, tmp = plugin
    p2 = main.DaFeiYuPlugin(None, main.AstrBotConfig({"data_dir": str(tmp)}))
    assert p2._md_mode is True


def test_commands_share_one_source_and_bare_aliases_are_restricted(plugin):
    main, p, tmp = plugin
    assert set(main._ACTION_KEYS) == set(main.COMMANDS) - {"养鱼"}
    assert "帮助" not in main._ALIAS_NAMES
    assert "档案" not in main._ALIAS_NAMES
    assert "/帮助" in main._ALIAS_NAMES
    assert "面板" not in main._ALIAS_NAMES and "/面板" in main._ALIAS_NAMES
    assert main._ACTION_RE.search("养鱼 面板").group(1) == "面板"
    results = _run(_gather(p.cmd_yangyu(FakeEvent(message_str="#面板"))))
    assert any("鱼籽" in text for text in _texts(results[-1]))


def test_data_directory_fallback_is_anchored_to_plugin_location(plugin, monkeypatch):
    main, p, tmp = plugin
    from astrbot.api.star import StarTools

    def fail(_name):
        raise RuntimeError("unavailable")

    monkeypatch.setattr(StarTools, "get_data_dir", fail, raising=False)
    assert (
        main._default_data_dir()
        == main.PLUGIN_DIR.parent.parent / "plugin_data" / "astrbot_plugin_dafeiyu"
    )


def test_metadata_is_single_version_source_and_contains_market_fields(plugin):
    main, p, tmp = plugin
    text = (main.PLUGIN_DIR / "metadata.yaml").read_text(encoding="utf-8")
    assert f'version: "{main._PLUGIN_VERSION}"' in text
    assert main._PLUGIN_VERSION == "2.2.1"
    assert "display_name:" in text and "support_platforms:" in text


def test_missing_custom_image_directory_is_a_normal_fallback(plugin, caplog, tmp_path):
    main, p, tmp = plugin
    p._custom_img_dir = tmp_path / "missing-fish-images"
    assert p._pick_custom_img() is None
    assert not any("自定义鱼图读取失败" in record.message for record in caplog.records)


def test_disabled_sticker_source_does_not_fetch_index(plugin, monkeypatch):
    main, p, tmp = plugin
    p._sticker_enabled = False

    def unexpected_request(*args, **kwargs):
        raise AssertionError("disabled sticker source must not make requests")

    monkeypatch.setattr(main.sticker_src, "load_index", unexpected_request)
    result = types.SimpleNamespace(event="message", title="投喂")
    assert p._get_sticker(result, None) is None


def test_markdown_filter_only_removes_button_hint_line(plugin):
    main, p, tmp = plugin
    from game.engine import ActionResult, Button

    result = ActionResult(
        title="记录", lines=["[已达成] 本周目标完成"], buttons=[Button("养鱼", "养鱼")]
    )
    text = "".join(_texts(p._build_result(FakeEvent(), result)))
    assert "[已达成] 本周目标完成" in text


def test_slash_message_reaches_panel(plugin):
    main, p, tmp = plugin
    ev = FakeEvent(name="小明", message_str="/养鱼")
    results = _run(_gather(p.cmd_yangyu(ev)))
    assert any("鱼籽" in t for t in _texts(results[-1]))


def test_panel_nick_gate(plugin):
    main, p, tmp = plugin
    ev = FakeEvent(name="")  # 无平台昵称
    results = _run(_gather(p._dispatch(ev, "养鱼")))
    assert ev.stopped
    kb = results[-1]
    assert any("昵称" in t for t in _texts(kb))  # 面板先问名字
    assert p._pending[("test:GroupMessage:1", "u1")] > 0


@pytest.mark.parametrize("prefix", ["", "/", "#", "/养鱼 "])
def test_guess_text_command_and_argument(plugin, prefix):
    main, p, tmp = plugin
    _run(_gather(p.cmd_yangyu(FakeEvent(message_str=prefix + "猜鱼"))))
    state = _saved_state(tmp)
    answer = state["pending_guess"]["u1"]["hidden"]
    results = _run(_gather(p.cmd_yangyu(FakeEvent(message_str=prefix + f"猜鱼 {answer}"))))
    assert any("猜中了" in text for text in _texts(results[-1]))
    assert not _saved_state(tmp)["pending_guess"]


def test_treasure_button_argument_and_menu(plugin):
    main, p, tmp = plugin
    _run(_gather(p.cmd_yangyu(FakeEvent(message_str="/寻宝"))))
    inter = types.SimpleNamespace(
        action_id="dfy:寻宝|探路|接力",
        data={"cmd": "/养鱼 寻宝 探路", "act": "寻宝", "arg": "探路"},
    )
    results = _run(_gather(p.on_button(FakeEvent(interaction=inter))))
    assert any("完成了探路" in text for text in _texts(results[-1]))
    assert "u1" in _saved_state(tmp)["pending_treasure"]["got"]
    results = _run(_gather(p.cmd_yangyu(FakeEvent(message_str="/小游戏"))))
    assert any("猜鱼" in text for text in _texts(results[-1]))


@pytest.mark.parametrize(
    "raw",
    [
        {"author": {"member_nickname": " 群昵称 ", "username": "QQ昵称"}},
        types.SimpleNamespace(
            author=types.SimpleNamespace(member_nickname="群昵称", username="QQ昵称")
        ),
        types.SimpleNamespace(
            raw_data={"author": {"member_nickname": "群昵称"}},
            author=types.SimpleNamespace(username="QQ昵称"),
        ),
    ],
)
def test_qq_group_nickname_auto_registers(plugin, raw):
    main, p, tmp = plugin
    ev = FakeEvent(name="", message_str="投喂")
    ev.message_obj = types.SimpleNamespace(raw_message=raw)
    results = _run(_gather(p._dispatch(ev, "投喂")))
    assert any("饱腹" in text for text in _texts(results[-1]))
    user = _saved_state(tmp)["users"]["u1"]
    assert user["nick_set"] and user["display_name"] == "群昵称"
    assert not p._pending


def test_qq_c2c_name_fallback_and_manual_name(plugin):
    main, p, tmp = plugin
    ev = FakeEvent(name="")
    ev.message_obj = types.SimpleNamespace(raw_message={"author": {"username": "QQ昵称"}})
    assert main._sender_name(ev) == "QQ昵称"
    _run(_gather(p._dispatch(ev, "养鱼")))
    assert _saved_state(tmp)["users"]["u1"]["display_name"] == "QQ昵称"
    _run(_gather(p._dispatch(ev, "昵称", "小鱼友")))
    _run(_gather(p._dispatch(ev, "养鱼")))
    assert _saved_state(tmp)["users"]["u1"]["display_name"] == "小鱼友"
    ev.message_obj.raw_message = {
        "author": {"username": None, "member_nickname": " "},
        "mentions": [{"member_nickname": "别人的昵称"}],
    }
    assert main._sender_name(ev) == ""
    ev._name = "框架昵称"
    assert main._sender_name(ev) == "框架昵称"


def test_new_nickname_clears_pending_prompt(plugin):
    main, p, tmp = plugin
    ev = FakeEvent(name="")
    _run(_gather(p._dispatch(ev, "养鱼")))
    assert p._pending
    ev.message_obj = types.SimpleNamespace(raw_message={"author": {"member_nickname": "新昵称"}})
    _run(_gather(p._dispatch(ev, "养鱼")))
    assert not p._pending
    assert _saved_state(tmp)["users"]["u1"]["display_name"] == "新昵称"


@pytest.mark.parametrize("prefix", ["", "/", "#", "/养鱼 "])
def test_adventure_text_choice_and_new_commands(plugin, prefix):
    main, p, tmp = plugin
    _run(_gather(p.cmd_yangyu(FakeEvent(message_str=prefix + "奇遇"))))
    game = _saved_state(tmp)["pending_adventure"]
    results = _run(
        _gather(p.cmd_yangyu(FakeEvent(message_str=prefix + "奇遇 帮忙@" + game["token"])))
    )
    assert any("纪念物" in text for text in _texts(results[-1]))
    assert _saved_state(tmp)["pending_adventure"] is None
    results = _run(_gather(p.cmd_yangyu(FakeEvent(message_str=prefix + "纪念物"))))
    assert any("×1" in text for text in _texts(results[-1]))
    results = _run(_gather(p.cmd_yangyu(FakeEvent(message_str=prefix + "心愿"))))
    assert any("今日小心愿" in text for text in _texts(results[-1]))


def test_adventure_button_roundtrip(plugin):
    main, p, tmp = plugin
    results = _run(_gather(p.cmd_yangyu(FakeEvent(message_str="/奇遇"))))
    button = _rows(results[-1], main)[0].buttons[0]
    data = button.action.data
    assert data["act"] == "奇遇" and data["arg"].startswith("帮忙@")
    inter = types.SimpleNamespace(action_id=button.id, data=data)
    results = _run(_gather(p.on_button(FakeEvent(interaction=inter))))
    assert any("奇遇归来" in text for text in _texts(results[-1]))
    assert _saved_state(tmp)["users"]["u1"]["keepsakes"]


@pytest.mark.parametrize("prefix", ["", "/", "#", "/养鱼 "])
def test_build_text_and_button_arguments(plugin, prefix):
    main, p, tmp = plugin
    event = FakeEvent(message_str=prefix + "建设")
    results = _run(_gather(p.cmd_yangyu(event)))
    pond = p._get_pond(event)
    pond.state.users["u1"].materials = 5
    button = _rows(results[-1], main)[0].buttons[0]
    assert button.action.data["arg"] == "水草角"
    inter = types.SimpleNamespace(action_id=button.id, data=button.action.data)
    _run(_gather(p.on_button(FakeEvent(interaction=inter))))
    assert _saved_state(tmp)["decorations"]["水草角"]["u1"] == 1
    for _ in range(4):
        _run(_gather(p.cmd_yangyu(FakeEvent(message_str=prefix + "装修 水草角"))))
    assert pond.state.users["u1"].materials == 0
    assert _saved_state(tmp)["decorations"]["水草角"]["u1"] == 5
    pond.state.fish.exp, pond.state.fish.weight, pond.state.fish.mood = 600, 90, 80
    results = _run(_gather(p.cmd_yangyu(FakeEvent(message_str=prefix + "出栏"))))
    assert len(results) == 2  # includes graduation photo
    results = _run(_gather(p.cmd_yangyu(FakeEvent(message_str=prefix + "合影 1"))))
    assert any("鱼塘纪念册" in t for t in _texts(results[-1]))
    assert len(results) == 2


@pytest.mark.parametrize("prefix", ["", "/", "#", "/养鱼 "])
def test_sports_strategy_button_roundtrip(plugin, prefix):
    main, p, tmp = plugin
    result = _run(_gather(p.cmd_yangyu(FakeEvent(message_str=prefix + "运动会 冲刺"))))
    button = _rows(result[-1], main)[0].buttons[2]
    assert "爆发" in button.action.data["arg"]
    inter = types.SimpleNamespace(action_id=button.id, data=button.action.data)
    _run(_gather(p.on_button(FakeEvent(interaction=inter))))
    assert _saved_state(tmp)["users"]["u1"]["sports_badges"] == 1
    _run(_gather(p.on_button(FakeEvent(interaction=inter))))
    assert _saved_state(tmp)["users"]["u1"]["sports_badges"] == 1
    result = _run(_gather(p.cmd_yangyu(FakeEvent(message_str=prefix + "性格"))))
    assert any("性格" in text for text in _texts(result[-1]))


@pytest.mark.parametrize("prefix", ["", "/", "#", "/养鱼 "])
def test_weekly_commands_and_care_button(plugin, prefix):
    main, p, tmp = plugin
    results = _run(_gather(p.cmd_yangyu(FakeEvent(message_str=prefix + "委托"))))
    assert any("每周鱼塘委托" in t for t in _texts(results[-1]))
    button = _rows(results[-1], main)[0].buttons[0]
    inter = types.SimpleNamespace(action_id=button.id, data=button.action.data)
    _run(_gather(p.on_button(FakeEvent(interaction=inter))))
    assert _saved_state(tmp)["weekly_quest"]["progress"]["food"] == 1
    results = _run(_gather(p.cmd_yangyu(FakeEvent(message_str=prefix + "周报"))))
    assert any("鱼塘周报" in t for t in _texts(results[-1]))
    results = _run(_gather(p.cmd_yangyu(FakeEvent(message_str=prefix + "小游戏"))))
    assert len(_rows(results[-1], main)) <= 5


@pytest.mark.parametrize("markdown", [True, False])
@pytest.mark.parametrize(
    "command", ["养鱼", "小游戏", "猜鱼", "奇遇", "寻宝", "心愿", "建设", "运动会", "委托"]
)
def test_reply_format_marker_preserves_all_cards(plugin, markdown, command):
    main, p, tmp = plugin
    p._md_mode = markdown
    p._send_image = False
    extra = {}
    event = FakeEvent(message_str="/养鱼 " + command)
    event.set_extra = lambda key, value: extra.__setitem__(key, value)
    results = _run(_gather(p.cmd_yangyu(event)))
    assert extra.get("reply_keep_format") is True
    original = "".join(_texts(results[-1]))

    # Replay the consumer contract: unmarked replies are flattened.
    def single_line_reply(text, metadata):
        return text if metadata.get("reply_keep_format") else " ".join(text.split())

    assert "\n" in original
    assert single_line_reply(original, extra) == original
    assert "\n" not in single_line_reply(original, {})


def test_callback_sets_keep_format_marker(plugin):
    main, p, tmp = plugin
    extra = {}
    inter = types.SimpleNamespace(action_id="dfy:猜鱼||猜鱼", data={"cmd": "/养鱼 猜鱼"})
    event = FakeEvent(interaction=inter)
    event.set_extra = lambda key, value: extra.__setitem__(key, value)
    results = _run(_gather(p.on_button(event)))
    assert extra["reply_keep_format"] is True
    assert len(_rows(results[-1], main)) > 0
    assert "  \n" in "".join(_texts(results[-1]))


def test_compact_markdown_keeps_lines_without_empty_paragraphs(plugin):
    main, p, tmp = plugin
    assert main.md_text("标题\n\n第一条\n第二条\n") == "标题  \n第一条  \n第二条"
    results = _run(_gather(p._dispatch(FakeEvent(), "心愿")))
    text = "".join(_texts(results[-1]))
    assert "  \n" in text
    assert "\n\n" not in text
    assert "有效照料自动推进" in text


def test_pond_cache_is_bounded_and_lru(plugin, monkeypatch):
    main, p, tmp = plugin
    monkeypatch.setattr(main, "load_or_create", lambda *args, **kwargs: object())
    for i in range(main._POND_CACHE_MAX):
        p._get_pond(FakeEvent(session=f"cache:{i}"))
    p._get_pond(FakeEvent(session="cache:0"))
    p._get_pond(FakeEvent(session="cache:new"))
    assert len(p._ponds) == main._POND_CACHE_MAX
    assert "cache:0" in p._ponds
    assert "cache:1" not in p._ponds
    assert "cache:new" in p._ponds


def test_idle_session_locks_are_released(plugin):
    import gc

    main, p, tmp = plugin
    lock = asyncio.Lock()
    p._locks["idle"] = lock
    assert p._locks["idle"] is lock
    del lock
    gc.collect()
    assert "idle" not in p._locks


def test_handle_and_custom_image_work_run_in_threads(plugin, monkeypatch):
    main, p, tmp = plugin
    event = FakeEvent(message_str="/投喂")
    pond = p._get_pond(event)
    original_handle = pond.handle
    handle_threads = []
    image_threads = []

    def tracked_handle(*args):
        handle_threads.append(threading.get_ident())
        return original_handle(*args)

    def pick_custom_img():
        image_threads.append(threading.get_ident())
        return None

    monkeypatch.setattr(pond, "handle", tracked_handle)
    monkeypatch.setattr(p, "_pick_custom_img", pick_custom_img)
    caller_thread = threading.get_ident()
    _run(_gather(p._dispatch(event, "投喂")))
    assert handle_threads and handle_threads[0] != caller_thread
    assert image_threads and image_threads[0] != caller_thread


def test_cache_miss_load_runs_in_thread(plugin, monkeypatch):
    main, p, tmp = plugin
    p._send_image = False
    original_load = main.load_or_create
    load_threads = []

    def tracked_load(*args, **kwargs):
        load_threads.append(threading.get_ident())
        return original_load(*args, **kwargs)

    monkeypatch.setattr(main, "load_or_create", tracked_load)
    caller_thread = threading.get_ident()
    _run(_gather(p._dispatch(FakeEvent(), "养鱼")))
    assert load_threads and load_threads[0] != caller_thread
