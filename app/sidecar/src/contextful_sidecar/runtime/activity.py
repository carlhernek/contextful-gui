"""Per-run module activity transcript (JSONL under runs/<runId>/<moduleId>/)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ACTIVITY_FILE = "activity.jsonl"
_seq: dict[str, int] = {}


def _activity_key(workspace: Path, run_id: str, module_id: str) -> str:
    return f"{workspace.resolve().as_posix()}:{run_id}:{module_id}"


def _next_seq(key: str) -> int:
    _seq[key] = _seq.get(key, 0) + 1
    return _seq[key]


def activity_path(workspace: Path, run_id: str, module_id: str) -> Path:
    return Path(workspace) / "runs" / run_id / module_id / ACTIVITY_FILE


def append_activity(
    workspace: Path,
    run_id: str,
    module_id: str,
    kind: str,
    **fields: Any,
) -> None:
    """Append one JSON line to runs/<runId>/<moduleId>/activity.jsonl (fail-safe)."""
    if not run_id or not module_id:
        return
    key = _activity_key(workspace, run_id, module_id)
    entry: dict[str, Any] = {
        "seq": _next_seq(key),
        "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "kind": kind,
    }
    entry.update(fields)
    path = activity_path(workspace, run_id, module_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_activity(workspace: Path, run_id: str, module_id: str) -> list[dict[str, Any]]:
    path = activity_path(workspace, run_id, module_id)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    out.append(parsed)
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return out
