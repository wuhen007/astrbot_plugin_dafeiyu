"""《肥鱼养成记》核心包：纯游戏逻辑，无框架依赖。"""
from .config import FISH_NAMES, STAGE_EXP, STAGE_NAMES, GameConfig
from .engine import COMMANDS, ActionResult, Button, Pond
from .state import FishState, PondState, UserInfo
from .store import JsonStore, load_or_create

__all__ = [
    "GameConfig", "Pond", "PondState", "FishState", "UserInfo",
    "ActionResult", "Button", "COMMANDS", "JsonStore", "load_or_create",
    "STAGE_NAMES", "STAGE_EXP", "FISH_NAMES",
]
