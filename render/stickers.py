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

INDEX_TTL = 6 * 3600      # 索引缓存 6 小时
UA = "astrbot_plugin_dafeiyu (+https://github.com/wuhen007/astrbot_plugin_dafeiyu)"
_FISH_MARKS = ("鱼", "鲸鱼", "肥鱼")   # alt 含这些字样的优先（蓝色大肥鱼本尊）


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


def pick_sticker(stickers, tags_want, alt_keywords, rng=None, prefer_fish=True):
    """按 tags 精确命中(×3) + alt 关键词命中(×1) 打分取最高；并列随机。
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
    best, best_score = None, -1.0
    want = set(tags_want or [])
    for s in pool:
        score = 3.0 * len(want & set(s.get("tags") or []))
        alt = s.get("alt", "")
        score += sum(1.0 for k in (alt_keywords or []) if k in alt)
        score += rng.random() * 0.5
        if score > best_score:
            best, best_score = s, score
    return best


def download_sticker(sticker, cache_dir):
    """下载 large 图到本地（按 url 哈希缓存），返回本地路径或 None。"""
    url = sticker.get("large") or sticker.get("preview") or sticker.get("original")
    if not url:
        return None
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(url).suffix or ".webp"
    p = cache_dir / f"{hashlib.sha1(url.encode('utf-8')).hexdigest()}{ext}"
    if p.exists() and p.stat().st_size > 0:
        return str(p)
    try:
        data = _http_get(url, timeout=20)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)
        return str(p)
    except Exception:
        return None
