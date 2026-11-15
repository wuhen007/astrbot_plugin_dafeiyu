"""贴图源模块测试（纯逻辑，不联网）。"""
import io
import json
import os
import time
from pathlib import Path

from render import stickers


def _fake(n=1):
    return [{"alt": "蓝色大肥鱼开心地游来游去", "tags": ["开心"], "large": "https://x/1.webp"},
            {"alt": "大肥鱼抱着白饭猛炫", "tags": ["吃饭"], "large": "https://x/2.webp"},
            {"alt": "ChatGPT娘站在栏杆上", "tags": ["搞笑"], "large": "https://x/3.webp"}]


def test_pick_prefers_fish_and_tags():
    s = stickers.pick_sticker(_fake(), ["吃饭"], ["白饭"], rng=None, prefer_fish=True)
    assert s["large"] == "https://x/2.webp"       # 大肥鱼系 + 吃饭 tag 得分最高
    s2 = stickers.pick_sticker(_fake(), ["开心"], [], rng=None, prefer_fish=True)
    assert s2["large"] == "https://x/1.webp"


def test_pick_fallback_to_all_pool():
    """没有大肥鱼系时退回全池。"""
    pool = [{"alt": "小猫睡觉", "tags": ["睡觉"], "large": "https://x/cat.webp"}]
    s = stickers.pick_sticker(pool, ["睡觉"], [], prefer_fish=True)
    assert s["large"] == "https://x/cat.webp"


def test_pick_empty():
    assert stickers.pick_sticker([], ["吃饭"], []) is None


def test_load_index_cache_hit(tmp_path):
    p = tmp_path / "index.json"
    p.write_text(json.dumps({"stickers": _fake()}), encoding="utf-8")
    st = stickers.load_index(tmp_path, "https://invalid.invalid/index.json", ttl=3600)
    assert st and st[0]["alt"].startswith("蓝色大肥鱼")   # 新缓存直接命中，不联网


def test_load_index_stale_cache_fallback(tmp_path, monkeypatch):
    p = tmp_path / "index.json"
    p.write_text(json.dumps({"stickers": _fake()}), encoding="utf-8")
    old = time.time() - stickers.INDEX_TTL - 10
    os.utime(p, (old, old))                       # 缓存过期
    monkeypatch.setattr(stickers, "_http_get",
                        lambda url, timeout=12: (_ for _ in ()).throw(OSError("net down")))
    st = stickers.load_index(tmp_path, "https://invalid.invalid/index.json")
    assert st and st[0]["large"] == "https://x/1.webp"   # 联网失败回退旧缓存


def test_load_index_total_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(stickers, "_http_get",
                        lambda url, timeout=12: (_ for _ in ()).throw(OSError("net down")))
    assert stickers.load_index(tmp_path, "https://invalid.invalid/index.json") is None


def test_download_sticker_converts_webp_to_png(tmp_path, monkeypatch):
    """QQ 官方富媒体不支持 webp：下载后自动转 PNG（透明底贴白）。"""
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (40, 30), (10, 20, 30, 200)).save(buf, "WEBP")
    monkeypatch.setattr(stickers, "_http_get", lambda url, timeout=20: buf.getvalue())
    s = {"large": "https://x/large.webp"}
    out = stickers.download_sticker(s, tmp_path / "img")
    assert out and Path(out).suffix == ".png"
    img = Image.open(out)
    assert img.format == "PNG" and img.mode == "RGB"
    out2 = stickers.download_sticker(s, tmp_path / "img")
    assert Path(out2).read_bytes() == Path(out).read_bytes()   # 二次调用命中缓存


def test_download_sticker_keeps_gif(tmp_path, monkeypatch):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("P", (20, 20), 3).save(buf, "GIF")
    monkeypatch.setattr(stickers, "_http_get", lambda url, timeout=20: buf.getvalue())
    s = {"large": "https://x/anim.gif"}
    out = stickers.download_sticker(s, tmp_path / "img")
    assert Path(out).suffix == ".gif"          # gif 受支持，保留动画


def test_ensure_png_for_custom_file(tmp_path):
    from PIL import Image
    webp = tmp_path / "fish_imgs" / "a.webp"
    webp.parent.mkdir()
    Image.new("RGBA", (30, 30), (1, 2, 3, 255)).save(webp, "WEBP")
    out = stickers.ensure_png(str(webp), tmp_path / "conv")
    assert Path(out).suffix == ".png"
    img = Image.open(out)
    assert img.mode == "RGB"
    jpg = tmp_path / "b.jpg"
    Image.new("RGB", (20, 20), (9, 9, 9)).save(jpg, "JPEG")
    assert stickers.ensure_png(str(jpg), tmp_path / "conv") == str(jpg)   # jpg 原样
