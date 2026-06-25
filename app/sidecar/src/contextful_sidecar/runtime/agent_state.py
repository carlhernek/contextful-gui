"""Per-module agent checkpoint so a resumed run continues instead of restarting.

The checkpoint stores the full LLM message transcript plus turn/progress flags
under ``runs/<runId>/<moduleId>/.agent-state.json``. On resume the agent reloads
it and keeps going from the next turn — already-read files and already-written
artifacts are not redone. It is removed once the module completes successfully.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from contextful_sidecar.runtime.eventlog import append_eventlog

AGENT_STATE_FILE = ".agent-state.json"
STATE_VERSION = 1


def _state_path(workspace: Path, run_id: str, module_id: str) -> Path:
    return Path(workspace) / "runs" / run_id / module_id / AGENT_STATE_FILE


def save_agent_state(
    workspace: Path,
    run_id: str,
    module_id: str,
    *,
    turn: int,
    messages: list[dict[str, Any]],
    wrote_analysis: bool,
    wrote_tasks: bool,
    fetch_refunds: int = 0,
) -> None:
    """Persist the agent transcript + progress after a turn (best-effort)."""
    if not run_id or not module_id:
        return
    payload = {
        "version": STATE_VERSION,
        "turn": turn,
        "wroteAnalysis": wrote_analysis,
        "wroteTasks": wrote_tasks,
        "fetchRefunds": fetch_refunds,
        "messages": messages,
    }
    path = _state_path(workspace, run_id, module_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except (OSError, TypeError, ValueError):
        # Checkpointing must never abort a run.
        pass


def load_agent_state(
    workspace: Path, run_id: str, module_id: str
) -> dict[str, Any] | None:
    """Return a saved checkpoint dict, or None if absent/unreadable/incompatible."""
    path = _state_path(workspace, run_id, module_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
        return None
    msgs = data.get("messages")
    if not isinstance(msgs, list) or not msgs:
        return None
    return data


def clear_agent_state(workspace: Path, run_id: str, module_id: str) -> None:
    """Remove the checkpoint (called on success or when forcing a fresh attempt)."""
    path = _state_path(workspace, run_id, module_id)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def log_resume(workspace: Path, module_id: str, turn: int, total_msgs: int) -> None:
    append_eventlog(
        workspace,
        module_id,
        "RESUME",
        f"continuing from turn {turn} ({total_msgs} prior messages restored)",
    )
