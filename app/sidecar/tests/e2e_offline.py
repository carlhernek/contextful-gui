#!/usr/bin/env python3
"""Offline end-to-end test of the analysis pipeline (no network / no API key).

Drives run_modules with a fake OpenRouter client that emits tool calls, proving the
full chain: orchestration -> agent loop -> tools -> sandboxed writes -> run state ->
schema-validated artifacts. Run with: uv run python tests/e2e_offline.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from contextful_sidecar.runtime.runs import load_run_state, run_modules  # noqa: E402
from contextful_sidecar.runtime.schema import validate_tasks  # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"OK: {name}")
    else:
        FAIL += 1
        print(f"FAIL: {name}")


class FakeClient:
    """Emits write_analysis + write_tasks on the first turn, then stops."""

    def __init__(self) -> None:
        self.turns = 0

    async def chat_completion(self, *, model, messages, tools=None, on_token=None):  # noqa: ARG002
        self.turns += 1
        if on_token:
            on_token("thinking… ")
        if self.turns == 1:
            tasks = {
                "moduleId": "security-analysis",
                "runId": RUN_ID,
                "tasks": [
                    {
                        "id": "SEC-001",
                        "title": "Sanitize user input in /search",
                        "priority": "high",
                        "effort": "S",
                        "evidence": ["repos/web/src/app.py:2"],
                        "rationale": "eval on user input enables RCE",
                        "agentic_spec": "Replace eval with a safe parser; add a regression test.",
                    }
                ],
            }
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {
                                        "name": "write_analysis",
                                        "arguments": json.dumps(
                                            {"module_id": "security-analysis",
                                             "content": "# Security Analysis\n\nFound eval() RCE."}
                                        ),
                                    },
                                },
                                {
                                    "id": "c2",
                                    "type": "function",
                                    "function": {
                                        "name": "write_tasks",
                                        "arguments": json.dumps(
                                            {"module_id": "security-analysis",
                                             "tasks_json": json.dumps(tasks)}
                                        ),
                                    },
                                },
                            ],
                        }
                    }
                ]
            }
        return {"choices": [{"message": {"role": "assistant",
                "content": "Analysis complete: 1 high-priority finding."}}]}


def _make_workspace(root: Path) -> Path:
    ws = root / "project"
    (ws / "repos" / "web" / "src").mkdir(parents=True)
    (ws / "modules" / "security-analysis").mkdir(parents=True)
    (ws / "templates").mkdir(parents=True)
    (ws / "meta").mkdir(parents=True)
    (ws / "research").mkdir(parents=True)
    (ws / "runs").mkdir(parents=True)
    (ws / "agents").mkdir(parents=True)
    (ws / "agents" / "workspace-orchestrator.md").write_text(
        "# Workspace Orchestrator\n\nE2E policy layer.\n", encoding="utf-8"
    )
    (ws / "agents" / "module-agent.md").write_text(
        "# Module Agent\n\nE2E module role.\n", encoding="utf-8"
    )
    (ws / ".eventlog").write_text("", encoding="utf-8")
    (ws / "modules" / "template-version.txt").write_text("1.0.0\n", encoding="utf-8")
    (ws / "modules" / "security-analysis" / "SKILL.md").write_text(
        "# Security Analysis\n\nFind security issues.\n", encoding="utf-8"
    )
    (ws / "repos" / "web" / "src" / "app.py").write_text(
        "def handler():\n    eval(user_input)\n", encoding="utf-8"
    )
    return ws


RUN_ID = "20260615-120000-e2e0"


async def main_async(ws: Path) -> None:
    events: list[str] = []
    summary = await run_modules(
        workspace=str(ws),
        client=FakeClient(),
        models={"orchestrator": "fake", "module": "fake"},
        run_id=RUN_ID,
        modules=["security-analysis"],
        project_type="both",
        resume=True,
        force=False,
        on_event=lambda ev, data: events.append(ev),
        should_cancel=lambda: False,
    )

    check("run completed", summary["status"] == "complete")
    check("module marked completed", "security-analysis" in summary["completedModules"])

    analysis = ws / "runs" / RUN_ID / "security-analysis" / "analysis.md"
    tasks = ws / "runs" / RUN_ID / "security-analysis" / "tasks.json"
    check("analysis.md written", analysis.exists())
    check("tasks.json written", tasks.exists())

    if tasks.exists():
        doc = json.loads(tasks.read_text(encoding="utf-8"))
        check("tasks.json validates against schema", validate_tasks(doc) is None)

    state = load_run_state(ws, RUN_ID)
    check("run-state persisted as complete", state["status"] == "complete")
    check("run-summary.md written", (ws / "runs" / RUN_ID / "run-summary.md").exists())
    check("streamed module + run + token events", {"module", "run", "token"} <= set(events))

    # Resume is a no-op once complete.
    again = await run_modules(
        workspace=str(ws), client=FakeClient(),
        models={"orchestrator": "fake", "module": "fake"},
        run_id=RUN_ID, modules=["security-analysis"], resume=True, force=False,
    )
    check("resume on complete run is a no-op", again.get("alreadyComplete") is True)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        ws = _make_workspace(Path(tmp))
        asyncio.run(main_async(ws))
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
