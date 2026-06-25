"""Stuck-turn auto-abort and checkpoint resume for the module agent."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from contextful_sidecar.runtime.agent import run_agent  # noqa: E402
from contextful_sidecar.runtime.agent_state import (  # noqa: E402
    load_agent_state,
    save_agent_state,
)


def _make_workspace(tmp_path: Path) -> tuple[Path, Path]:
    ws = tmp_path / "project"
    (ws / "repos" / "web").mkdir(parents=True)
    schema_src = (
        Path(__file__).resolve().parents[4]
        / "contextful-files" / "templates" / "tasks.schema.json"
    )
    (ws / "templates").mkdir()
    (ws / "templates" / "tasks.schema.json").write_text(
        schema_src.read_text(encoding="utf-8"), encoding="utf-8"
    )
    skill = ws / "modules" / "security-analysis" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# Security\n", encoding="utf-8")
    (ws / "agents").mkdir()
    (ws / "agents" / "workspace-orchestrator.md").write_text("# ws\n", encoding="utf-8")
    (ws / "agents" / "module-agent.md").write_text("# mod\n", encoding="utf-8")
    (ws / ".eventlog").write_text("", encoding="utf-8")
    return ws, skill


def _read_file_call(call_id: str, path: str) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "read_file", "arguments": json.dumps({"path": path})},
    }


def _write_outputs_message() -> dict:
    tasks = {
        "moduleId": "security-analysis",
        "runId": "run-1",
        "tasks": [{
            "id": "SEC-001", "title": "Fix thing", "priority": "high", "effort": "S",
            "evidence": ["repos/web/a.ts:1"], "rationale": "because",
            "agentic_spec": "In repos/web/, fix a.ts.",
        }],
    }
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "w1", "type": "function", "function": {
                "name": "write_analysis",
                "arguments": json.dumps({"module_id": "security-analysis",
                                         "content": "# Analysis\n\nDone."})}},
            {"id": "w2", "type": "function", "function": {
                "name": "write_tasks",
                "arguments": json.dumps({"module_id": "security-analysis",
                                         "tasks_json": json.dumps(tasks)})}},
        ],
    }


class _StuckClient:
    """Always re-issues the same failing tool call (read a missing file)."""

    def __init__(self) -> None:
        self.calls = 0

    async def chat_completion(self, *, model, messages, tools=None, on_token=None):  # noqa: ARG002
        self.calls += 1
        return {"choices": [{"message": {
            "role": "assistant", "content": "trying again",
            "tool_calls": [_read_file_call("c", "does-not-exist.txt")],
        }}]}


def test_agent_aborts_when_stuck(tmp_path: Path):
    ws, skill = _make_workspace(tmp_path)
    client = _StuckClient()
    result = asyncio.run(run_agent(
        workspace=ws, instruction_file=skill, model="fake", client=client,
        role="Security Analysis", module_id="security-analysis", run_id="run-1",
        repo_paths=[ws / "repos" / "web"], meta_docs=[], project_type="both",
        max_turns=24,
    ))
    assert result.rstrip().endswith("(stuck)"), result
    # Aborted well before the turn budget rather than spinning to max_turns.
    assert client.calls < 24
    assert "STUCK_ABORT" in (ws / ".eventlog").read_text(encoding="utf-8")


class _ResumeAwareClient:
    """Completes immediately and records how many messages it first received."""

    def __init__(self) -> None:
        self.calls = 0
        self.first_message_count = 0

    async def chat_completion(self, *, model, messages, tools=None, on_token=None):  # noqa: ARG002
        self.calls += 1
        if self.calls == 1:
            self.first_message_count = len(messages)
        return {"choices": [{"message": _write_outputs_message()}]}


def test_agent_resumes_from_checkpoint(tmp_path: Path):
    ws, skill = _make_workspace(tmp_path)
    # Simulate a prior partial run: a transcript with several turns already done.
    prior_messages = [
        {"role": "system", "content": "SYSTEM PROMPT (restored)"},
        {"role": "user", "content": "Begin the Security Analysis analysis now."},
        {"role": "assistant", "content": "", "tool_calls": [_read_file_call("a", "repos/web/a.ts")]},
        {"role": "tool", "tool_call_id": "a", "content": "file contents already read"},
        {"role": "assistant", "content": "", "tool_calls": [_read_file_call("b", "repos/web/b.ts")]},
        {"role": "tool", "tool_call_id": "b", "content": "more file contents already read"},
    ]
    save_agent_state(
        ws, "run-1", "security-analysis",
        turn=7, messages=prior_messages, wrote_analysis=False, wrote_tasks=False,
    )

    client = _ResumeAwareClient()
    result = asyncio.run(run_agent(
        workspace=ws, instruction_file=skill, model="fake", client=client,
        role="Security Analysis", module_id="security-analysis", run_id="run-1",
        repo_paths=[ws / "repos" / "web"], meta_docs=[], project_type="both",
        max_turns=24, resume_checkpoint=True,
    ))
    # Resumed transcript (6 prior messages) was used, not a fresh 2-message start.
    assert client.first_message_count >= 6
    assert "complete" in result.lower()
    assert (ws / "runs" / "run-1" / "security-analysis" / "analysis.md").is_file()
    # Checkpoint cleared on success.
    assert load_agent_state(ws, "run-1", "security-analysis") is None


def test_fresh_run_ignores_existing_checkpoint(tmp_path: Path):
    ws, skill = _make_workspace(tmp_path)
    save_agent_state(
        ws, "run-1", "security-analysis",
        turn=5, messages=[{"role": "system", "content": "STALE"}],
        wrote_analysis=False, wrote_tasks=False,
    )
    client = _ResumeAwareClient()
    asyncio.run(run_agent(
        workspace=ws, instruction_file=skill, model="fake", client=client,
        role="Security Analysis", module_id="security-analysis", run_id="run-1",
        repo_paths=[ws / "repos" / "web"], meta_docs=[], project_type="both",
        max_turns=24, resume_checkpoint=False,
    ))
    # Fresh start: system + user only (2 messages), stale checkpoint discarded.
    assert client.first_message_count == 2
