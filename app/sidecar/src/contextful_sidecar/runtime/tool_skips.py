"""Persist per-module tool skips under runs/<runId>/<moduleId>/skips.json."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SKIPS_FILE = "skips.json"


def skips_path(workspace: Path, run_id: str, module_id: str) -> Path:
    return Path(workspace) / "runs" / run_id / module_id / SKIPS_FILE


def read_skips(workspace: Path, run_id: str, module_id: str) -> list[dict[str, Any]]:
    path = skips_path(workspace, run_id, module_id)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def append_skip(
    workspace: Path,
    run_id: str,
    module_id: str,
    *,
    name: str,
    args: dict[str, Any],
    attempts: int,
    reason: str,
    duration_ms: int,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": name,
        "args": args,
        "attempts": attempts,
        "reason": reason[:500],
        "durationMs": duration_ms,
        "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
    path = skips_path(workspace, run_id, module_id)
    existing = read_skips(workspace, run_id, module_id)
    existing.append(entry)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    except OSError:
        pass
    return entry
