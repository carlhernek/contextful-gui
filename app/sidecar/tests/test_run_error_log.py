"""Run failures append ERROR lines to .eventlog."""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from contextful_sidecar.runtime.activity import read_activity
from contextful_sidecar.runtime.index_agent import MODULE_ID
from contextful_sidecar.runtime.runs import load_run_state, run_modules


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
                app_version="1.2.8",
            )
            assert result["status"] == "failed"
            log = (ws / ".eventlog").read_text(encoding="utf-8")
            assert "missing-module ERROR" in log
            assert "run ERROR" in log
            assert "fail-run" in log
            assert "app=v1.2.8" in log

    asyncio.run(run())


def test_cancelled_run_persists_terminal_state_and_event():
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
            (ws / "modules" / "workspace-index").mkdir(parents=True)
            (ws / "modules" / "workspace-index" / "SKILL.md").write_text("# Index\n", encoding="utf-8")

            cancelled = False

            def should_cancel() -> bool:
                return cancelled

            events: list[tuple[str, object]] = []

            async def slow_reindex(**kwargs):
                nonlocal cancelled
                cancelled = True
                raise asyncio.CancelledError()

            with patch(
                "contextful_sidecar.runtime.runs.agentic_reindex",
                side_effect=slow_reindex,
            ):
                result = await run_modules(
                    workspace=str(ws),
                    client=FakeClient(),  # type: ignore[arg-type]
                    models={"module": "test/model"},
                    run_id="cancel-run",
                    modules=["workspace-index"],
                    app_version="1.2.8",
                    on_event=lambda ev, data: events.append((ev, data)),
                    should_cancel=should_cancel,
                )
                assert result["status"] == "cancelled"

            state = load_run_state(ws, "cancel-run")
            assert state["status"] == "cancelled"
            log = (ws / ".eventlog").read_text(encoding="utf-8")
            assert "run CANCELLED" in log
            assert any(ev == "run" and (d := data).get("status") == "cancelled" for ev, data in events)

    asyncio.run(run())


def test_agentic_run_emits_granular_log_steps():
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
            (ws / "repos" / "web" / "README.md").write_text("# Web\n", encoding="utf-8")
            (ws / "modules" / "workspace-index").mkdir(parents=True)
            (ws / "modules" / "workspace-index" / "SKILL.md").write_text("# Index\n", encoding="utf-8")

            client = AsyncMock()
            client.chat_completion.return_value = {
                "choices": [{
                    "message": {
                        "content": '{"description":"indexed","keywords":["x"]}',
                    },
                }],
            }

            await run_modules(
                workspace=str(ws),
                client=client,
                models={"module": "test/model"},
                run_id="granular-run",
                modules=["workspace-index"],
                app_version="1.2.8",
            )

            log = (ws / ".eventlog").read_text(encoding="utf-8")
            for marker in (
                "SCAN_START",
                "SCAN_DONE",
                "LLM_REQUEST",
                "LLM_RESPONSE",
                "INDEX_START",
                "INDEX_DONE",
            ):
                assert marker in log, f"missing {marker} in eventlog"

            activity = read_activity(ws, "granular-run", MODULE_ID)
            kinds = {a["kind"] for a in activity}
            assert "llm_request" in kinds
            assert "llm_response" in kinds

    asyncio.run(run())
