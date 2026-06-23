"""ToolTrace and tool_runner progress/stall logging."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import patch

from contextful_sidecar.runtime.tool_runner import run_tool_with_liveness
from contextful_sidecar.runtime.tool_trace import ToolTrace
from contextful_sidecar.runtime.tools import execute_tool


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "project"
    (ws / "repos" / "web").mkdir(parents=True)
    (ws / "runs" / "run-1" / "mod").mkdir(parents=True)
    return ws


def test_tool_trace_summary_includes_phase_and_path():
    trace = ToolTrace()
    trace.set_phase("walk")
    trace.tick("files", 42, path="repos/API/src/foo.ts")
    summary = trace.summary()
    assert "phase=walk" in summary
    assert "files=42" in summary
    assert "last=repos/API/src/foo.ts" in summary


def test_tool_runner_timeout_includes_trace_and_no_retry(tmp_path: Path):
    ws = _ws(tmp_path)
    attempts = {"n": 0}

    def slow(_ws: Path, _name: str, _args: dict, _trace: ToolTrace) -> str:
        attempts["n"] += 1
        time.sleep(0.2)
        return "ok"

    async def run():
        with patch("contextful_sidecar.runtime.tool_runner.TOOL_TIMEOUT_SEC", 0.05):
            with patch("contextful_sidecar.runtime.tool_runner.PROGRESS_INTERVAL_SEC", 0.02):
                return await run_tool_with_liveness(
                    workspace=ws,
                    log_scope="mod",
                    turn=1,
                    name="gather_context",
                    args={"path": "repos/web"},
                    run_id="run-1",
                    module_id="mod",
                    tool_executor=slow,
                )

    result = asyncio.run(run())
    assert attempts["n"] == 1
    assert "timed out" in result.lower()
    assert "phase=" in result


def test_gather_context_single_git_subprocess(tmp_path: Path, monkeypatch):
    ws = _ws(tmp_path)
    repo = ws / "repos" / "web"
    (repo / ".git").mkdir()
    (repo / "README.md").write_text("# Web\n", encoding="utf-8")
    for i in range(300):
        (repo / f"file{i}.txt").write_text(f"content {i}\n", encoding="utf-8")

    calls: list[list[str]] = []

    def spy(*args, **kwargs):
        cmd = list(args[0]) if args else []
        calls.append(cmd)
        return __import__("subprocess").CompletedProcess(args=args, returncode=1, stdout="", stderr="")

    monkeypatch.setattr("contextful_sidecar.runtime.repo_path_policy._silent_run", spy)

    out = execute_tool(ws, "gather_context", {"path": "repos/web"})
    assert "Context bundle" in out
    check_ignore_calls = [c for c in calls if "check-ignore" in " ".join(c)]
    assert len(check_ignore_calls) <= 1
