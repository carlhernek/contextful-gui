"""Security: write sandbox, SSRF, path policy matrix, gather_context bounds, tool runner."""
from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

from contextful_sidecar.runtime.repo_path_policy import check_repo_path
from contextful_sidecar.runtime.ssrf_guard import validate_fetch_url
from contextful_sidecar.runtime.tool_runner import (
    MAX_TOOL_RETRIES,
    run_tool_with_liveness,
)
from contextful_sidecar.runtime.tool_skips import read_skips
from contextful_sidecar.runtime.tools import execute_tool, set_run_context
from contextful_sidecar.runtime.write_policy import check_write_allowed


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "project"
    (ws / "repos" / "web" / "src").mkdir(parents=True)
    (ws / "runs" / "run-1" / "mod").mkdir(parents=True)
    (ws / "research").mkdir(parents=True)
    (ws / ".eventlog").write_text("", encoding="utf-8")
    return ws


def test_write_blocked_outside_runs_and_research(tmp_path: Path):
    ws = _ws(tmp_path)
    set_run_context(ws, "run-1")
    assert "blocked" in execute_tool(ws, "write_file", {
        "path": "repos/web/evil.txt", "content": "x",
    }).lower()
    assert "blocked" in execute_tool(ws, "write_file", {
        "path": "modules/foo/SKILL.md", "content": "x",
    }).lower()
    ok = execute_tool(ws, "write_file", {
        "path": "runs/run-1/mod/note.txt", "content": "ok",
    })
    assert ok.startswith("wrote")
    ok2 = execute_tool(ws, "write_file", {
        "path": "research/note.txt", "content": "ok",
    })
    assert ok2.startswith("wrote")


def test_write_policy_allowlist():
    ws = Path("/tmp/ws")
    run_id = "run-abc"
    assert check_write_allowed(ws, ws / "runs" / run_id / "m" / "a.md", run_id) is None
    assert check_write_allowed(ws, ws / "research" / "x.json", run_id) is None
    assert check_write_allowed(ws, ws / "repos" / "web" / "x", run_id) is not None


def test_ssrf_blocks_loopback_and_metadata():
    assert validate_fetch_url("http://127.0.0.1/") is not None
    assert validate_fetch_url("http://localhost/") is not None
    assert validate_fetch_url("http://169.254.169.254/") is not None


def test_path_policy_blocks_sensitive_and_gitignored(tmp_path: Path):
    ws = _ws(tmp_path)
    repo = ws / "repos" / "web"
    (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (repo / "id_rsa").write_text("key\n", encoding="utf-8")
    (repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (repo / "ignored").mkdir()
    (repo / "ignored" / "secret.txt").write_text("x\n", encoding="utf-8")

    for tool, args in [
        ("read_file", {"path": "repos/web/.env"}),
        ("read_file", {"path": "repos/web/id_rsa"}),
        ("read_file", {"path": "repos/web/ignored/secret.txt"}),
    ]:
        out = execute_tool(ws, tool, args)
        assert "blocked" in out.lower()

    out = execute_tool(ws, "gather_context", {"path": "repos/web"})
    assert "SECRET=1" not in out


def test_gather_context_skips_git_and_is_fast(tmp_path: Path):
    ws = _ws(tmp_path)
    repo = ws / "repos" / "web"
    (repo / "README.md").write_text("# Web\n", encoding="utf-8")
    (repo / "Cargo.toml").write_text('[package]\nname = "api"\n', encoding="utf-8")
    git_dir = repo / ".git" / "objects"
    git_dir.mkdir(parents=True)
    for i in range(200):
        (git_dir / f"obj{i}").write_bytes(b"\x00" * 100)

    t0 = time.monotonic()
    out = execute_tool(ws, "gather_context", {"path": "repos/web"})
    elapsed = time.monotonic() - t0
    assert elapsed < 5.0
    assert "Context bundle" in out
    assert "obj0" not in out


def test_check_repo_path_no_subprocess_for_git(tmp_path: Path, monkeypatch):
    ws = _ws(tmp_path)
    repo = ws / "repos" / "web"
    (repo / ".git").mkdir()
    target = repo / ".git" / "config"
    target.write_text("[core]\n", encoding="utf-8")

    calls: list[list[str]] = []

    def spy(*args, **kwargs):
        calls.append(list(args[0]) if args else [])
        return subprocess.CompletedProcess(args=args, returncode=1)

    monkeypatch.setattr(
        "contextful_sidecar.runtime.repo_path_policy._silent_run",
        spy,
    )
    result = check_repo_path(ws, target)
    assert result is not None
    assert ".git" in result
    assert calls == []


def test_tool_runner_skips_after_retries(tmp_path: Path):
    ws = _ws(tmp_path)

    def flaky(_ws: Path, _name: str, _args: dict) -> str:
        return "ERROR: connection reset by peer"

    async def run():
        with patch("contextful_sidecar.runtime.tool_runner.TOOL_RETRY_BASE_DELAY_SEC", 0.01):
            return await run_tool_with_liveness(
                workspace=ws,
                log_scope="mod",
                turn=1,
                name="gather_context",
                args={"path": "repos/web"},
                run_id="run-1",
                module_id="mod",
                tool_executor=flaky,
            )

    result = asyncio.run(run())
    assert "skipped" in result.lower()
    skips = read_skips(ws, "run-1", "mod")
    assert len(skips) == 1
    assert skips[0]["name"] == "gather_context"


def test_tool_runner_no_retry_on_deterministic_error(tmp_path: Path):
    ws = _ws(tmp_path)
    attempts = {"n": 0}

    def deterministic(_ws: Path, _name: str, _args: dict) -> str:
        attempts["n"] += 1
        return "ERROR: file not found"

    async def run():
        return await run_tool_with_liveness(
            workspace=ws,
            log_scope="mod",
            turn=1,
            name="read_file",
            args={"path": "repos/web/missing.ts"},
            run_id="run-1",
            module_id="mod",
            tool_executor=deterministic,
        )

    result = asyncio.run(run())
    assert attempts["n"] == 1
    assert "skipped" not in result.lower()
    assert read_skips(ws, "run-1", "mod") == []


def test_tool_runner_timeout_no_retry(tmp_path: Path):
    ws = _ws(tmp_path)
    attempts = {"n": 0}

    def slow(_ws: Path, _name: str, _args: dict) -> str:
        attempts["n"] += 1
        time.sleep(0.2)
        return "ok"

    async def run():
        with patch("contextful_sidecar.runtime.tool_runner.TOOL_TIMEOUT_SEC", 0.05):
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
    assert read_skips(ws, "run-1", "mod") == []
