"""Load per-module runtime limits from modules/module-config.json."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

CONFIG_REL = Path("modules") / "module-config.json"
WORKSPACE_INDEX_MODULE = "workspace-index"

DEFAULT_MAX_TURNS = 24
DEFAULT_INDEX_MAX_TURNS = 8
MIN_TURNS = 4
MAX_TURNS = 64


def _clamp_turns(value: int) -> int:
    return max(MIN_TURNS, min(MAX_TURNS, int(value)))


@lru_cache(maxsize=8)
def _load_config(workspace_key: str) -> dict[str, Any] | None:
    path = Path(workspace_key) / CONFIG_REL
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _module_entry(config: dict[str, Any] | None, module_id: str) -> dict[str, Any]:
    if not config:
        return {}
    modules = config.get("modules")
    if not isinstance(modules, dict):
        return {}
    entry = modules.get(module_id)
    return entry if isinstance(entry, dict) else {}


def _default_max_turns(config: dict[str, Any] | None) -> int:
    if not config:
        return DEFAULT_MAX_TURNS
    defaults = config.get("defaults")
    if not isinstance(defaults, dict):
        return DEFAULT_MAX_TURNS
    raw = defaults.get("maxTurns", DEFAULT_MAX_TURNS)
    try:
        return _clamp_turns(int(raw))
    except (TypeError, ValueError):
        return DEFAULT_MAX_TURNS


def get_max_turns(workspace: Path, module_id: str) -> int:
    """Turn budget for a standard analysis module run."""
    config = _load_config(str(Path(workspace).resolve()))
    entry = _module_entry(config, module_id)
    raw = entry.get("maxTurns")
    if raw is None:
        return _default_max_turns(config)
    try:
        return _clamp_turns(int(raw))
    except (TypeError, ValueError):
        return _default_max_turns(config)


def get_index_max_turns(workspace: Path) -> int:
    """Turn budget per indexed item for workspace-index."""
    config = _load_config(str(Path(workspace).resolve()))
    entry = _module_entry(config, WORKSPACE_INDEX_MODULE)
    raw = entry.get("maxTurnsPerItem")
    if raw is None:
        return DEFAULT_INDEX_MAX_TURNS
    try:
        return _clamp_turns(int(raw))
    except (TypeError, ValueError):
        return DEFAULT_INDEX_MAX_TURNS


def clear_config_cache() -> None:
    _load_config.cache_clear()
