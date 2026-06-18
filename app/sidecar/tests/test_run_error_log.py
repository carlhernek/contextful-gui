"""Run failures append ERROR lines to .eventlog."""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from contextful_sidecar.runtime.runs import run_modules


class FakeClient:
    async def chat_completion(self, **kwargs):
        return {"choices": [{"message": {"content": "ok"}}]}


def test_missing_module_skill_logs_run_error():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            (ws / ".contextful.json").write_text(
                json.dumps({
                    "display_name": "x",
                    "project_type": "both",
                    "repos": [],
                }),
                encoding="utf-8",
            )
            (ws / "modules" / "missing-module").mkdir(parents=True)

            result = await run_modules(
                workspace=str(ws),
                client=FakeClient(),  # type: ignore[arg-type]
                models={"module": "test/model"},
                run_id="fail-run",
                modules=["missing-module"],
            )
            assert result["status"] == "failed"
            log = (ws / ".eventlog").read_text(encoding="utf-8")
            assert "missing-module ERROR" in log
            assert "run ERROR" in log
            assert "fail-run" in log

    asyncio.run(run())
