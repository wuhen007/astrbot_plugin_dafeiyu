"""fisharchive.cc 贴图源：索引缓存 + 按语义挑图 + 图片下载缓存。

索引 https://fisharchive.cc/stickers/index.json（CC BY-NC-SA 4.0，需署名）。
每张：alt（中文描述）、tags（中文分类，如 吃饭/开心/委屈）、large（webp）。
所有网络操作为同步阻塞，调用方需放线程里（asyncio.to_thread）；
任何失败返回 None，调用方回退内置画鱼。
"""
from __future__ import annotations

import hashlib
import json
import random
import time
import urllib.request
from pathlib import Path

INDEX_TTL = 24 * 3600     # 索引缓存 24 小时（减少对素材站的请求）
UA = "astrbot_plugin_dafeiyu (+https://github.com/wuhen007/astrbot_plugin_dafeiyu)"
_FISH_MARKS = ("鱼", "鲸鱼", "肥鱼")   # alt 含这些字样的优先（蓝色大肥鱼本尊）
# QQ 官方富媒体支持的格式；webp 会被拒（错误 850019 富媒体文件格式不支持）
SUPPORTED_RAW = (".jpg", ".jpeg", ".png", ".gif")


def load_manifest(cache_dir) -> dict:
    """已下载贴图的本地清单：{large_url: 本地路径}。"""
    p = Path(cache_dir) / "manifest.json"
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:
        return {}


def save_manifest(cache_dir, manifest: dict) -> None:
    p = Path(cache_dir) / "manifest.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False), "utf-8")
    tmp.replace(p)


def _http_get(url: str, timeout: int = 12) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def load_index(cache_dir: Path, url: str, ttl: int = INDEX_TTL):
    """取贴图索引：新鲜缓存 → 网络刷新 → 旧缓存兜底；全失败返回 None。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    p = cache_dir / "index.json"
    try:
        if p.exists() and time.time() - p.stat().st_mtime < ttl:
            return json.loads(p.read_text("utf-8")).get("stickers")
        data = json.loads(_http_get(url).decode("utf-8"))
        stickers = data.get("stickers") or []
        if stickers:
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
            tmp.replace(p)
            return stickers
    except Exception:
        pass
    try:
        if p.exists():
            return json.loads(p.read_text("utf-8")).get("stickers")
    except Exception:
        pass
    return None


def pick_sticker(stickers, tags_want, alt_keywords, rng=None, prefer_fish=True,
                 cached_urls=None):
    """按 tags 精确命中(×3) + alt 关键词命中(×1) 打分；同分候选里**优先选
    已缓存到本地的**（不再请求素材站），否则随机挑一张新图去下载。
    prefer_fish=True 时优先在"大肥鱼系"贴图（alt 含 鱼/鲸鱼/肥鱼）里挑。"""
    if not stickers:
        return None
    rng = rng or random.Random()
    if prefer_fish:
        pool = [s for s in stickers
                if any(k in s.get("alt", "") for k in _FISH_MARKS)]
        if not pool:            # 索引里没有大肥鱼系时退回全池
            pool = list(stickers)
    else:
        pool = list(stickers)
    want = set(tags_want or [])
    scored = []
    for s in pool:
        score = 3.0 * len(want & set(s.get("tags") or []))
        alt = s.get("alt", "")
        score += sum(1.0 for k in (alt_keywords or []) if k in alt)
        scored.append((score, s))
    if not scored:
        return None
    best_score = max(score for score, _ in scored)
    top = [s for score, s in scored if score == best_score]
    cached = [s for s in top if s.get("large") in (cached_urls or set())]
    return rng.choice(cached or top)


def _to_png(data: bytes) -> bytes:
    """任意图片字节 → PNG（透明底贴白，动图取首帧）。QQ 官方富媒体
    不支持 webp，这里统一归一化。需要 Pillow。"""
    import io

    from PIL import Image
    img = Image.open(io.BytesIO(data))
    if getattr(img, "is_animated", False):
        img.seek(0)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def ensure_png(path, cache_dir) -> str:
    """本地图片若不是 QQ 支持的格式（如 webp）→ 转存 PNG 并缓存。
    jpg/jpeg/gif/png 原样返回。"""
    p = Path(path)
    if p.suffix.lower() in SUPPORTED_RAW:
        return str(p)
    data = p.read_bytes()
    key = hashlib.sha1(data).hexdigest()
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"conv-{key}.png"
    if out.exists() and out.stat().st_size > 0:
        return str(out)
    out.write_bytes(_to_png(data))
    return str(out)


def download_sticker(sticker, cache_dir, manifest=None):
    """下载贴图到本地（按 url 哈希缓存 + 记入清单）；非 QQ 支持格式自动转
    PNG。已缓存过则不再请求素材站。返回本地路径或 None。"""
    url = sticker.get("large") or sticker.get("preview") or sticker.get("original")
    if not url:
        return None
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest = {} if manifest is None else manifest
    if url in manifest and Path(manifest[url]).exists():
        return manifest[url]          # 已缓存，直接复用
    ext = Path(url).suffix.lower() or ".webp"
    raw = cache_dir / f"{hashlib.sha1(url.encode('utf-8')).hexdigest()}{ext}"
    if not raw.exists() or raw.stat().st_size == 0:
        try:
            data = _http_get(url, timeout=20)
            tmp = raw.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(raw)
        except Exception:
            return None
    if ext in SUPPORTED_RAW:
        local = str(raw)
    else:
        try:
            png = cache_dir / f"{raw.stem}.png"
            if not png.exists() or png.stat().st_size == 0:
                png.write_bytes(_to_png(raw.read_bytes()))
            local = str(png)
        except Exception:
            return None
    manifest[url] = local
    save_manifest(cache_dir, manifest)
    return local
