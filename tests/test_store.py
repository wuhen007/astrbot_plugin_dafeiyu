"""存储层测试。"""
import random

from game import GameConfig, Pond
from game.store import JsonStore, load_or_create

T0 = 1_700_000_000.0


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
    assert loaded.fish.cooldowns["feed"] == pond.state.fish.cooldowns["feed"]
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


def test_corrupt_file_treated_as_new(tmp_path):
    store = JsonStore(tmp_path)
    p = store._path("bad:key")
    p.write_text("not json{{", encoding="utf-8")
    assert store.load("bad:key") is None


def test_session_key_sanitized(tmp_path):
    store = JsonStore(tmp_path)
    pond = Pond.create("aiocqhttp:GroupMessage:123456@!#", config=GameConfig(),
                       clock=lambda: T0, rng=random.Random(1))
    store.save(pond.state)
    assert store.load("aiocqhttp:GroupMessage:123456@!#") is not None
