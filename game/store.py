"""JSON 持久化：一座鱼塘一个文件。

接口稳定（load/save/load_or_create），以后可平滑换成 sqlite。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Optional

from astrbot.api import logger

from .config import GameConfig
from .engine import Pond
from .state import PondState


class JsonStore:
    def __init__(self, base_dir):
        self.base = Path(base_dir)
        self.base.mkdir(parents=True, exist_ok=True)

    def _path(self, session_key: str) -> Path:
        safe = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", session_key)[:48] or "pond"
        digest = hashlib.md5(session_key.encode("utf-8")).hexdigest()[:8]
        return self.base / f"{safe}-{digest}.json"

    def load(self, session_key: str) -> Optional[PondState]:
        p = self._path(session_key)
        if not p.exists():
            return None
        try:
            raw = json.loads(p.read_text("utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("存档根节点必须是对象")
            expected = {"fish": dict, "users": dict, "archive": list,
                        "pending_guess": dict, "pending_travel": dict, "story_flags": list,
                        "decorations": dict, "round_stats": dict,
                        "round_events": list, "weekly_reports": list,
                        "daily_diary": dict, "minigames_unlocked": bool}
            for key, kind in expected.items():
                if key in raw and raw[key] is not None and not isinstance(raw[key], kind):
                    raise ValueError(f"存档字段 {key} 类型错误")
            return PondState.from_dict(raw)
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            backup = p.with_name(f"{p.stem}.bad-{time.time_ns()}{p.suffix}")
            try:
                p.replace(backup)
            except OSError as backup_error:
                logger.error("[肥鱼养成记] 存档损坏且无法备份 %s：%s", p, backup_error)
                raise RuntimeError(f"鱼塘存档损坏且无法备份：{p.name}") from backup_error
            logger.warning("[肥鱼养成记] 存档损坏，已备份为 %s：%s", backup.name, e)
            return None

    def save(self, state: PondState) -> None:
        p = self._path(state.session_key)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, p)


def load_or_create(store: JsonStore, session_key: str,
                   config: Optional[GameConfig] = None, clock=None, rng=None) -> Pond:
    """取回鱼塘；Pond 已挂上 store，后续 handle 自动落盘。"""
    state = store.load(session_key)
    if state is None:
        pond = Pond.create(session_key, config=config, clock=clock, rng=rng, store=store)
    else:
        pond = Pond(state, config=config, clock=clock, rng=rng, store=store)
    return pond
