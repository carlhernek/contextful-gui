"""Workspace Index module runs deterministically via refresh_index (no LLM agent)."""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from contextful_sidecar.runtime.indexing import INDEX_FILE
from contextful_sidecar.runtime.runs import WORKSPACE_INDEX_MODULE, _order_modules, run_modules


class FakeClient:
    async def chat_completion(self, **kwargs):
        return {
            "choices": [
                {"message": {"content": '{"description":"indexed","keywords":["idx"]}'}}
            ]
        }


def test_workspace_index_module_writes_index():
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
            (ws / "meta").mkdir()
            (ws / "meta" / "requirements.md").write_text("# req\n", encoding="utf-8")
            (ws / "modules" / "workspace-index").mkdir(parents=True)
            (ws / "modules" / "workspace-index" / "SKILL.md").write_text(
                "# Workspace Index\n", encoding="utf-8"
            )

            result = await run_modules(
                workspace=str(ws),
                client=FakeClient(),  # type: ignore[arg-type]
                models={"module": "test/model"},
                run_id="idx-run",
                modules=[WORKSPACE_INDEX_MODULE],
            )
            assert result["status"] == "complete"
            assert WORKSPACE_INDEX_MODULE in result["completedModules"]
            assert (ws / INDEX_FILE).exists()
            index = json.loads((ws / INDEX_FILE).read_text(encoding="utf-8"))
            assert any(i["id"] == "meta:requirements.md" for i in index["items"])
            activity_path = ws / "runs" / "idx-run" / WORKSPACE_INDEX_MODULE / "activity.jsonl"
            assert activity_path.exists()

    asyncio.run(run())


def test_workspace_index_runs_last():
    ordered = _order_modules(["workspace-index", "security-analysis", "dependency-health"])
    assert ordered[-1] == "workspace-index"
    assert ordered[0] == "security-analysis"
