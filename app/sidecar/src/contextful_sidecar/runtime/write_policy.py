"""Enforce agent write allowlist: runs/<runId>/, research/, .eventlog only."""
from __future__ import annotations

from pathlib import Path


def check_write_allowed(workspace: Path, target: Path, run_id: str) -> str | None:
    """Return an ERROR message if agents may not write here, else None."""
    root = workspace.resolve()
    try:
        rel = target.resolve().relative_to(root)
    except ValueError:
        return "ERROR: write blocked (path escapes workspace)"

    parts = rel.parts
    if not parts:
        return "ERROR: write blocked (outside runs/ and research/)"

    if parts[0] == "research":
        return None

    if len(parts) == 1 and parts[0] == ".eventlog":
        return None

    if parts[0] == "runs" and len(parts) >= 2 and run_id and parts[1] == run_id:
        return None

    return "ERROR: write blocked (outside runs/ and research/)"
