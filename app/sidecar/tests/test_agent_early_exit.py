"""Module agent stops after required outputs are written."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from contextful_sidecar.runtime.agent import run_agent  # noqa: E402


class _FakeClient:
    def __init__(self) -> None:
        self.calls = 0

    async def chat_completion(self, *, model, messages, tools=None, on_token=None):  # noqa: ARG002
        self.calls += 1
        if self.calls == 1:
            tasks = {
                "moduleId": "security-analysis",
                "runId": "run-1",
                "tasks": [{
                    "id": "SEC-001",
                    "title": "Fix thing",
                    "priority": "high",
                    "effort": "S",
                    "evidence": ["repos/web/a.ts:1"],
                    "rationale": "because",
                    "agentic_spec": "In repos/web/, fix a.ts.",
                }],
            }
            return {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "1",
                                "type": "function",
                                "function": {
                                    "name": "write_analysis",
                                    "arguments": json.dumps({
                                        "module_id": "security-analysis",
                                        "content": "# Analysis\n\nDone.",
                                    }),
                                },
                            },
                            {
                                "id": "2",
                                "type": "function",
                                "function": {
                                    "name": "write_tasks",
                                    "arguments": json.dumps({
                                        "module_id": "security-analysis",
                                        "tasks_json": json.dumps(tasks),
                                    }),
                                },
                            },
                        ],
                    },
                }],
            }
        raise AssertionError("agent should not call LLM after outputs are written")


def test_agent_exits_after_write_outputs(tmp_path: Path):
    ws = tmp_path / "project"
    (ws / "repos" / "web").mkdir(parents=True)
    schema_src = (
        Path(__file__).resolve().parents[4]
        / "contextful-files"
        / "templates"
        / "tasks.schema.json"
    )
    (ws / "templates").mkdir()
    (ws / "templates" / "tasks.schema.json").write_text(
        schema_src.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    skill = ws / "modules" / "security-analysis" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# Security\n", encoding="utf-8")
    (ws / "agents").mkdir()
    (ws / "agents" / "workspace-orchestrator.md").write_text("# ws\n", encoding="utf-8")
    (ws / "agents" / "module-agent.md").write_text("# mod\n", encoding="utf-8")
    (ws / ".eventlog").write_text("", encoding="utf-8")

    client = _FakeClient()
    result = asyncio.run(run_agent(
        workspace=ws,
        instruction_file=skill,
        model="fake",
        client=client,
        role="Security Analysis",
        module_id="security-analysis",
        run_id="run-1",
        repo_paths=[ws / "repos" / "web"],
        meta_docs=[],
        project_type="both",
    ))
    assert client.calls == 1
    assert "complete" in result.lower()
    assert (ws / "runs" / "run-1" / "security-analysis" / "analysis.md").is_file()
    assert (ws / "runs" / "run-1" / "security-analysis" / "tasks.json").is_file()
