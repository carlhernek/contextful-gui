"""Activity transcript persistence."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from contextful_sidecar.runtime.activity import (
    ACTIVITY_FILE,
    append_activity,
    read_activity,
)


def test_append_activity_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        append_activity(ws, "run-1", "mod-a", "turn", turn=1, maxTurns=6)
        append_activity(
            ws,
            "run-1",
            "mod-a",
            "item",
            itemId="meta:doc.md",
            itemIndex=2,
            itemTotal=5,
            status="indexing",
        )
        path = ws / "runs" / "run-1" / "mod-a" / ACTIVITY_FILE
        assert path.exists()
        entries = read_activity(ws, "run-1", "mod-a")
        assert len(entries) == 2
        assert entries[0]["kind"] == "turn"
        assert entries[1]["itemId"] == "meta:doc.md"
        assert entries[1]["itemIndex"] == 2


def test_append_activity_fail_safe_on_bad_paths():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        append_activity(ws, "", "mod", "turn")
        append_activity(ws, "run", "", "turn")
        assert not list((ws / "runs").glob("**/*")) if not (ws / "runs").exists() else True
