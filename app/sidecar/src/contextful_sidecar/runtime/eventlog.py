"""Append-only event log convention (spec section 13)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

EVENTLOG_FILE = ".eventlog"


def append_eventlog(workspace: Path, scope: str, status: str, message: str = "") -> None:
    """Append one ISO-8601 (local tz) line to {workspace}/.eventlog.

    Format: [<ts>] <scope> <STATUS> — <message>
    scope is 'run', a module-id, or 'gui'.
    """
    ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    line = f"[{ts}] {scope} {status}"
    if message:
        line += f" — {message}"
    try:
        with (Path(workspace) / EVENTLOG_FILE).open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        # Never let logging failures abort an agent run.
        pass


def read_eventlog_tail(workspace: Path, max_lines: int = 100) -> list[str]:
    """Return the last max_lines lines of the event log (best-effort)."""
    path = Path(workspace) / EVENTLOG_FILE
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-max_lines:]
