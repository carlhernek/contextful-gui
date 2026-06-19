"""Scan trace and debug dump tests."""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from contextful_sidecar.runtime.indexing import (
    SCAN_DEBUG_FILE,
    ScanTrace,
    scan_items_async,
    write_scan_debug,
)


def test_scan_items_async_logs_each_repo_and_meta():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            (ws / ".contextful.json").write_text(
                json.dumps({
                    "display_name": "x",
                    "project_type": "both",
                    "repos": [{"name": "web", "url": "u", "branch": "main"}],
                }),
                encoding="utf-8",
            )
            (ws / "repos" / "web").mkdir(parents=True)
            (ws / "meta" / "docs").mkdir(parents=True)
            (ws / "meta" / "docs" / "a.md").write_text("# A\n", encoding="utf-8")

            events: list[tuple[str, object]] = []
            items, trace = await scan_items_async(
                ws,
                run_id="scan-run",
                include_artifacts=False,
                on_event=lambda ev, data: events.append((ev, data)),
            )
            ids = {i["id"] for i in items}
            assert "repo:web" in ids
            assert "meta:docs/a.md" in ids
            assert any(s.get("phase") == "repo_done" for s in trace.steps)
            assert any(s.get("phase") == "meta_done" for s in trace.steps)
            log = (ws / ".eventlog").read_text(encoding="utf-8")
            assert "SCAN_ITEM" in log
            assert any(ev == "index" and (d := data).get("phase") == "scan_item" for ev, data in events)

    asyncio.run(run())


def test_write_scan_debug_creates_pasteable_file():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "ws"
        ws.mkdir()
        trace = ScanTrace()
        trace.record("repo_start", itemId="repo:x")
        trace.record("repo_error", itemId="repo:x", error="git hung")
        path = write_scan_debug(ws, "dbg-run", trace, error="git hung", items=[])
        assert path.name == SCAN_DEBUG_FILE
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["error"] == "git hung"
        assert data["steps"][-1]["phase"] == "repo_error"
