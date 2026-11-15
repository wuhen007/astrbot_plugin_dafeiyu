"""main.py 接线冒烟测试：stub 掉 astrbot 框架后导入，模拟指令与按钮点击。"""
import asyncio
import importlib
import json
import logging
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- stub 框架
class FakeResult:
    def __init__(self, chain):
        self.chain = chain
        self.md = None

    def use_markdown(self, v):
        self.md = v
        return self


class FakeEvent:
    def __init__(self, session="test:GroupMessage:1", uid="u1", name="小明",
                 message_str="", interaction=None):
        self._session, self._uid, self._name = session, uid, name
        self.message_str = message_str
        self._interaction = interaction

    @property
    def unified_msg_origin(self):
        return self._session

    def get_sender_id(self):
        return self._uid

    def get_sender_name(self):
        return self._name

    def get_button_interaction(self):
        return self._interaction

    def chain_result(self, chain):
        return FakeResult(chain)


def _stub_astrbot(monkeypatch):
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

    class CallbackAction:
        def __init__(self, data=None, **kwargs):
            self.data = data

    class Button:
        def __init__(self, id=None, label=None, action=None, **kwargs):
            self.id, self.label, self.action = id, label, action

    class ActionRow:
        def __init__(self, buttons=None, fallback_text=None, **kwargs):
            self.buttons = buttons or []
            self.fallback_text = fallback_text

    mc.Plain, mc.Image, mc.CallbackAction, mc.Button, mc.ActionRow = \
        Plain, Image, CallbackAction, Button, ActionRow

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
    cfg = main.AstrBotConfig({"output_mode": "markdown", "send_image": True,
                              "difficulty": "标准", "data_dir": str(tmp_path)})
    return main, main.DaFeiYuPlugin(None, cfg), tmp_path


def _run(coro):
    return asyncio.run(coro)


async def _gather(agen):
    return [r async for r in agen]


def _plain_texts(result):
    return [c.text for c in result.chain if isinstance(c, __import__("main").Plain)]


# ---------------------------------------------------------------- 测试
def test_load_creates_data_dir(plugin):
    main, p, tmp = plugin
    assert (tmp / "ponds").is_dir()


def test_feed_via_dispatch(plugin):
    main, p, tmp = plugin
    ev = FakeEvent()
    results = _run(_gather(p._dispatch(ev, "投喂")))
    assert len(results) == 1
    assert results[0].md is True
    texts = _plain_texts(results[0])
    assert any("投喂" in t for t in texts)
    kinds = [type(c).__name__ for c in results[0].chain]
    assert "Image" in kinds and "ActionRow" in kinds
    # 存档自动落盘
    files = list((tmp / "ponds").glob("*.json"))
    assert files
    state = json.loads(files[0].read_text("utf-8"))
    assert state["fish"]["exp"] == 8


def test_button_click_routes_to_engine(plugin):
    main, p, tmp = plugin
    inter = types.SimpleNamespace(action_id="dfy:投喂", data="投喂")
    ev = FakeEvent(interaction=inter)
    results = _run(_gather(p.on_button(ev)))
    assert results
    assert any("投喂" in t for t in _plain_texts(results[0]))
    files = list((tmp / "ponds").glob("*.json"))
    assert json.loads(files[0].read_text("utf-8"))["fish"]["exp"] == 8


def test_button_play_pick(plugin):
    main, p, tmp = plugin
    p._dispatch  # noqa: B018
    ev_open = FakeEvent()
    _run(_gather(p._dispatch(ev_open, "玩耍")))
    files = list((tmp / "ponds").glob("*.json"))
    state = json.loads(files[0].read_text("utf-8"))
    hidden = state["pending_play"]["hidden"]
    inter = types.SimpleNamespace(action_id=f"dfy:玩耍 {hidden + 1}", data=f"玩耍 {hidden + 1}")
    ev = FakeEvent(interaction=inter)
    results = _run(_gather(p.on_button(ev)))
    texts = _plain_texts(results[0])
    assert any("体重 +3" in t for t in texts)


def test_button_foreign_ignored(plugin):
    main, p, tmp = plugin
    inter = types.SimpleNamespace(action_id="other:do", data="x")
    ev = FakeEvent(interaction=inter)
    results = _run(_gather(p.on_button(ev)))
    assert results == []


def test_yangyu_routes_shell_pick(plugin):
    main, p, tmp = plugin
    ev = FakeEvent(message_str="/养鱼 玩耍")
    results = _run(_gather(p.cmd_yangyu(ev)))
    assert any("套圈" in t for t in _plain_texts(results[0]))
    files = list((tmp / "ponds").glob("*.json"))
    state = json.loads(files[0].read_text("utf-8"))
    assert state["pending_play"] is not None


def test_button_rows_layout(plugin):
    main, p, tmp = plugin
    ev = FakeEvent()
    results = _run(_gather(p._dispatch(ev, "养鱼")))
    rows = [c for c in results[0].chain if isinstance(c, main.ActionRow)]
    assert rows
    assert [b.label for b in rows[0].buttons] == ["投喂", "加餐", "逗鱼"]
    btn = rows[0].buttons[0]
    assert btn.id == "dfy:投喂" and btn.action.data == "投喂"


def test_text_mode_forces_plain(plugin):
    main, p, tmp = plugin
    p._md_mode = False
    ev = FakeEvent()
    results = _run(_gather(p._dispatch(ev, "逗鱼")))
    assert results[0].md is False
    assert any("逗鱼" in t for t in _plain_texts(results[0]))


def test_slash_message_reaches_panel(plugin):
    """斜杠写法 /养鱼 走别名进来后，解析层能落回面板。"""
    main, p, tmp = plugin
    ev = FakeEvent(message_str="/养鱼")
    results = _run(_gather(p.cmd_yangyu(ev)))
    assert any("鱼籽" in t for t in _plain_texts(results[0]))


def test_default_mode_is_markdown(plugin):
    """未配置 output_mode 时默认 markdown（QQ 官方群/单聊 md 已全量开放）。"""
    main, p, tmp = plugin
    p2 = main.DaFeiYuPlugin(None, main.AstrBotConfig({"data_dir": str(tmp)}))
    assert p2._md_mode is True


def test_custom_fish_image_folder(plugin):
    """fish_imgs/ 里有图则随机用自定义形象，不再实时画鱼。"""
    main, p, tmp = plugin
    from PIL import Image as PILImage
    img_dir = tmp / "fish_imgs"
    img_dir.mkdir()
    PILImage.new("RGB", (32, 32), (10, 20, 30)).save(img_dir / "a.png")
    ev = FakeEvent()
    results = _run(_gather(p._dispatch(ev, "养鱼")))
    imgs = [c for c in results[0].chain if isinstance(c, main.Image)]
    assert imgs
    assert imgs[0].path.endswith("a.png")
