"""File preview for the UI (spec section 4: preview method)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from contextful_sidecar.runtime.tools import _resolve

PREVIEW_CAP = 200_000


def preview_file(workspace: Path, path: str, base: str = "repos") -> dict[str, Any]:
    """Return a preview dict for a file under <base>/ in the workspace.

    base is one of 'repos', 'runs', 'research', 'meta', etc. The combined path is
    sandboxed through _resolve so it can never escape the workspace.
    """
    workspace = Path(workspace)
    rel = path if base in ("", ".") else f"{base.rstrip('/')}/{path.lstrip('/')}"
    try:
        target = _resolve(workspace, rel)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "path": rel}
    if not target.exists():
        return {"ok": False, "error": "file not found", "path": rel}
    if target.is_dir():
        return {"ok": False, "error": "path is a directory", "path": rel}
    try:
        data = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"ok": False, "error": str(exc), "path": rel}
    truncated = len(data) > PREVIEW_CAP
    if truncated:
        data = data[:PREVIEW_CAP] + "\n...[truncated]"
    return {
        "ok": True,
        "path": rel,
        "name": target.name,
        "ext": target.suffix.lstrip("."),
        "size": target.stat().st_size,
        "truncated": truncated,
        "content": data,
    }
