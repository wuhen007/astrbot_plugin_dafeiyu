"""main.py 接线冒烟测试：stub 掉 astrbot 框架后导入，模拟指令/按钮/昵称登记。

v1.1.0 起发送结构 = 两条消息：①贴图/鱼图直接发原图（交互事件不发图）
②「md 文本卡 + 指令按钮」（data 带 {"cmd": "/养鱼 X"}，适配器补丁转指令按钮）。
"""
import asyncio
import importlib
import json
import logging
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

PLAIN_CLS = None    # 由 stub 注入
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
    def __init__(self, session="test:GroupMessage:1", uid="u1", name="小明",
                 message_str="", interaction=None):
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
    filter_pkg.event_message_type = lambda *a, **k: (lambda f: f)
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
    cfg = main.AstrBotConfig({"output_mode": "markdown", "send_image": True,
                              "difficulty": "标准", "data_dir": str(tmp_path)})
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
def test_load_creates_data_dir(plugin):
    main, p, tmp = plugin
    assert (tmp / "ponds").is_dir()


def test_feed_two_messages_image_then_kb(plugin):
    main, p, tmp = plugin
    ev = FakeEvent()
    results = _run(_gather(p._dispatch(ev, "投喂")))
    assert len(results) == 3                     # ① 面板图 ② 鱼图直发 ③ 摘要+按钮
    assert results[0].image is True and results[1].image is True
    kb = results[2]
    assert kb.md is True
    texts = "\n".join(_texts(kb))
    assert "【投喂】" in texts and "饱腹 +22" in texts
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
    assert ev2.stopped                           # 停住事件，别让其它插件/LLM 插话
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
    assert ("test:GroupMessage:1", "u1") in p._pending   # 引导仍挂起


def test_button_click_routes_and_skips_image(plugin):
    main, p, tmp = plugin
    inter = types.SimpleNamespace(action_id="dfy:投喂||投喂",
                                  data={"cmd": "/养鱼 投喂", "act": "投喂", "arg": None})
    ev = FakeEvent(interaction=inter)
    results = _run(_gather(p.on_button(ev)))
    assert len(results) == 1                     # 交互事件不发图
    assert results[0].image is False
    assert any("投喂" in t for t in _texts(results[0]))
    assert ev.stopped                            # 停事件，防 zhutian 插话
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
    assert [b.label for b in first] == ["投喂", "加餐", "逗鱼"]
    b0 = first[0]
    assert b0.id.startswith("dfy:")
    assert b0.action.data["cmd"] == "/养鱼 投喂"
    assert b0.action.data["act"] == "投喂"
    assert rows[0].action_timeout == 86400


def test_text_mode_uses_plain(plugin):
    main, p, tmp = plugin
    p._md_mode = False
    results = _run(_gather(p._dispatch(FakeEvent(), "逗鱼")))
    kb = results[-1]
    assert kb.md is None                         # plain_result 未走 use_markdown
    assert _rows(kb, main) == []                 # 纯文本模式不带键盘
    assert any("逗鱼" in t for t in _texts(kb))


def test_default_mode_is_markdown(plugin):
    main, p, tmp = plugin
    p2 = main.DaFeiYuPlugin(None, main.AstrBotConfig({"data_dir": str(tmp)}))
    assert p2._md_mode is True


def test_slash_message_reaches_panel(plugin):
    main, p, tmp = plugin
    ev = FakeEvent(name="小明", message_str="/养鱼")
    results = _run(_gather(p.cmd_yangyu(ev)))
    assert any("鱼籽" in t for t in _texts(results[-1]))


def test_panel_nick_gate(plugin):
    main, p, tmp = plugin
    ev = FakeEvent(name="")                      # 无平台昵称
    results = _run(_gather(p._dispatch(ev, "养鱼")))
    kb = results[-1]
    assert any("昵称" in t for t in _texts(kb))  # 面板先问名字
    assert p._pending[("test:GroupMessage:1", "u1")] > 0
