"""存储层测试。"""
import json
import random

import pytest

from game import GameConfig, Pond
from game.state import PondState
from game.store import JsonStore, load_or_create

T0 = 1_700_000_000.0


def test_daily_diary_is_optional_for_legacy_saves_and_roundtrips(tmp_path):
    legacy = PondState.from_dict({"session_key": "legacy:pond", "fish": {}})
    assert legacy.daily_diary is None
    assert legacy.to_dict()["daily_diary"] is None

    store = JsonStore(tmp_path)
    legacy_path = store._path("legacy:pond")
    legacy_path.write_text(json.dumps({"session_key": "legacy:pond", "fish": {}}), "utf-8")
    assert store.load("legacy:pond").daily_diary is None

    pond = Pond.create("diary:pond", config=GameConfig(), clock=lambda: T0)
    expected = {
        "day": "2026-11-22",
        "counts": {"投喂": 2},
        "highlights": ["遇见老朋友 Claude！偷偷带来零食。"],
        "stats": {"name": "团团", "mood": 82},
    }
    pond.state.daily_diary = expected
    store.save(pond.state)

    loaded = store.load("diary:pond")
    assert loaded.daily_diary == expected


def test_roundtrip(tmp_path):
    store = JsonStore(tmp_path)
    pond = Pond.create("test:group:1", config=GameConfig(),
                       clock=lambda: T0, rng=random.Random(7))
    pond.handle("u1", "小明", "投喂")
    store.save(pond.state)

    loaded = store.load("test:group:1")
    assert loaded is not None
    assert loaded.fish.satiety == pond.state.fish.satiety
    assert loaded.fish.weight == pond.state.fish.weight
    assert loaded.users["u1"].cooldowns["feed"] == pond.state.users["u1"].cooldowns["feed"]
    assert loaded.users["u1"].intimacy == 1
    assert loaded.community_exp == 1


def test_load_or_create(tmp_path):
    store = JsonStore(tmp_path)
    p1 = load_or_create(store, "key:A", config=GameConfig(),
                        clock=lambda: T0, rng=random.Random(1))
    p1.handle("u1", "小明", "投喂")
    p2 = load_or_create(store, "key:A", config=GameConfig(),
                        clock=lambda: T0, rng=random.Random(1))
    assert p2.state.fish.exp == p1.state.fish.exp
    assert "u1" in p2.state.users


def test_load_or_create_does_not_write_until_handle(tmp_path, monkeypatch):
    store = JsonStore(tmp_path)
    monkeypatch.setattr(store, "save", lambda state: pytest.fail("创建实例不应先写盘"))
    pond = load_or_create(store, "key:read-only-open", config=GameConfig(),
                          clock=lambda: T0, rng=random.Random(1))
    assert pond.state.fish.weight == GameConfig().start_weight


def test_corrupt_file_is_backed_up_before_new_state_is_saved(tmp_path):
    store = JsonStore(tmp_path)
    key = "bad:key"
    path = store._path(key)
    original = '{"fish": {"weight": 55.0}, "use'
    path.write_text(original, encoding="utf-8")

    pond = load_or_create(store, key, config=GameConfig(),
                          clock=lambda: T0, rng=random.Random(1))

    backups = list(tmp_path.glob("*.bad-*.json"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original
    assert pond.state.fish.weight == GameConfig().start_weight
    pond.handle("u1", "小明", "养鱼")
    assert json.loads(path.read_text(encoding="utf-8"))["fish"]["weight"] == GameConfig().start_weight


def test_corrupt_file_is_not_replaced_if_backup_fails(tmp_path, monkeypatch):
    store = JsonStore(tmp_path)
    key = "bad:key"
    path = store._path(key)
    original = "not json{{"
    path.write_text(original, encoding="utf-8")

    def fail_replace(self, target):
        raise OSError("read only")

    monkeypatch.setattr(type(path), "replace", fail_replace)
    with pytest.raises(RuntimeError, match="无法备份"):
        load_or_create(store, key, config=GameConfig(), clock=lambda: T0)
    assert path.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob("*.bad-*.json")) == []


def test_structurally_invalid_save_is_backed_up(tmp_path):
    store = JsonStore(tmp_path)
    key = "bad:shape"
    path = store._path(key)
    raw = '{"fish": []}'
    path.write_text(raw, encoding="utf-8")
    assert store.load(key) is None
    assert list(tmp_path.glob("*.bad-*.json"))[0].read_text(encoding="utf-8") == raw

    key = "bad:root"
    path = store._path(key)
    raw = "[]"
    path.write_text(raw, encoding="utf-8")
    assert store.load(key) is None
    backups = list(tmp_path.glob(f"{path.stem}.bad-*.json"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == raw


def test_session_key_sanitized(tmp_path):
    store = JsonStore(tmp_path)
    pond = Pond.create("aiocqhttp:GroupMessage:123456@!#", config=GameConfig(),
                       clock=lambda: T0, rng=random.Random(1))
    store.save(pond.state)
    assert store.load("aiocqhttp:GroupMessage:123456@!#") is not None
