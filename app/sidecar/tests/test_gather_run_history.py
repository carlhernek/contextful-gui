"""gather_run_history tool for suggested-next-steps."""
from __future__ import annotations

import json
from pathlib import Path

from contextful_sidecar.runtime.indexing import INDEX_FILE, refresh_index
from contextful_sidecar.runtime.runs import save_run_state
from contextful_sidecar.runtime.tools import execute_tool


def _write_project(ws: Path) -> None:
    ws.mkdir(parents=True, exist_ok=True)
    (ws / ".contextful.json").write_text(
        json.dumps({"display_name": "demo", "project_type": "b2b", "repos": []}),
        encoding="utf-8",
    )
    (ws / "meta").mkdir()
    (ws / "meta" / "brief.md").write_text("# Brief\n", encoding="utf-8")


def test_gather_run_history_initial_mode(tmp_path: Path):
    ws = tmp_path / "project"
    _write_project(ws)
    out = execute_tool(ws, "gather_run_history", {})
    data = json.loads(out)
    assert data["mode"] == "initial"
    assert data["anchorRunId"] is None
    assert data["delta"]["newItems"] == []


def test_gather_run_history_warm_mode_with_delta(tmp_path: Path):
    import asyncio

    ws = tmp_path / "project"
    _write_project(ws)

    run_id = "20260601-120000-aaaa"
    run_dir = ws / "runs" / run_id / "security-analysis"
    run_dir.mkdir(parents=True)
    (ws / "runs" / run_id / "run-summary.md").write_text("# Summary\n", encoding="utf-8")
    (run_dir / "tasks.json").write_text(
        json.dumps({
            "moduleId": "security-analysis",
            "runId": run_id,
            "tasks": [{
                "id": "SEC-001",
                "title": "Fix auth gap",
                "priority": "high",
                "effort": "M",
                "evidence": ["repos/web/auth.ts"],
                "rationale": "Critical",
                "agentic_spec": "Inspect auth.ts and add tests.",
            }],
        }),
        encoding="utf-8",
    )
    save_run_state(
        ws,
        run_id,
        status="complete",
        completedModules=["security-analysis", "workspace-index"],
    )
    state_path = ws / "runs" / run_id / ".run-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["updatedAt"] = "2026-06-01T12:00:00+02:00"
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    async def do_index():
        await refresh_index(workspace=ws, client=None, skip_enrichment=True)

    asyncio.run(do_index())
    index_doc = json.loads((ws / INDEX_FILE).read_text(encoding="utf-8"))
    for item in index_doc["items"]:
        item["indexedAt"] = "2026-06-02T10:00:00+02:00"
        item["contentUpdatedAt"] = item["indexedAt"]
    (ws / INDEX_FILE).write_text(json.dumps(index_doc), encoding="utf-8")

    (ws / "meta" / "new-doc.md").write_text("# New\n", encoding="utf-8")
    asyncio.run(do_index())

    out = execute_tool(ws, "gather_run_history", {})
    data = json.loads(out)
    assert data["mode"] == "warm"
    assert data["anchorRunId"] == run_id
    assert data["openTasks"]
    assert data["openTasks"][0]["taskId"] == "SEC-001"
    new_paths = {i.get("path") for i in data["delta"]["newItems"]}
    assert "meta/new-doc.md" in new_paths
