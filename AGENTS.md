# AstrBot Plugin Development Notes

Before changing plugin-facing code, follow the current AstrBot plugin guide:
https://docs.astrbot.app/dev/star/plugin-new.html

## Submission requirements

- Import the logger only with `from astrbot.api import logger`. Use that logger throughout plugin code; do not use Python's `logging` module or another logging package.
- Keep plugin metadata in `metadata.yaml` consistent with the release being prepared.
- Use AstrBot's public plugin APIs for event handlers, commands, buttons, configuration, and lifecycle integration. Treat framework internals as unstable and document any unavoidable compatibility shim.
- Keep user-facing text and command behavior compatible with the supported platform declared in `metadata.yaml`.

## This repository

- `main.py` is the AstrBot adapter and command/event entry point.
- `game/` contains game state, rules, and persistence; `render/` contains output rendering.
- Persistent state changes must remain readable from existing JSON saves. Add defaults for new fields and preserve unknown legacy inventory values.
- Tests run without AstrBot installed. Stub only the AstrBot API needed by tests; production modules should use the real public API.
- Before proposing a release, run `ruff check .` and `pytest -q`; report failures accurately. Do not push unless the user explicitly asks.
