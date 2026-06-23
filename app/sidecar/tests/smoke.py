#!/usr/bin/env python3
"""Offline smoke harness for the Contextful sidecar (no network / no API).

Run with: uv run python tests/smoke.py
Prints OK:/FAIL: per check and exits non-zero if any FAIL.
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

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from contextful_sidecar.runtime import tools  # noqa: E402
from contextful_sidecar.runtime.agents import (  # noqa: E402
    compose_module_prompt,
    compose_orchestrator_prompt,
    load_agent_doc,
)
from contextful_sidecar.runtime.chat import detect_run_intent  # noqa: E402
from contextful_sidecar.runtime.runs import filter_modules, save_run_state  # noqa: E402
from contextful_sidecar.runtime.schema import validate_tasks  # noqa: E402
from contextful_sidecar.runtime.indexing import INDEX_FILE, refresh_index  # noqa: E402
from contextful_sidecar.server import SidecarServer, _write_json  # noqa: E402
from legacy_fixture import (  # noqa: E402
    LEGACY_PROJECT_VERSIONS,
    assert_legacy_files_preserved,
    build_legacy_project,
    snapshot_text_files,
)

PASS = FAIL = 0


def check(name: str, condition: bool) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"OK: {name}")
    else:
        FAIL += 1
        print(f"FAIL: {name}")


def _seed_agent_docs(ws: Path) -> None:
    agents = ws / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "workspace-orchestrator.md").write_text(
        "# Workspace Orchestrator\n\nSMOKE_WS_POLICY_MARKER\n", encoding="utf-8"
    )
    (agents / "project-orchestrator.md").write_text(
        "# Project Orchestrator\n\nSMOKE_PROJ_ROLE_MARKER\n", encoding="utf-8"
    )
    (agents / "module-agent.md").write_text(
        "# Module Agent\n\nSMOKE_MOD_ROLE_MARKER\n", encoding="utf-8"
    )


def _make_workspace(root: Path) -> Path:
    ws = root / "ws"
    (ws / "repos" / "web" / "src").mkdir(parents=True)
    (ws / "scripts").mkdir(parents=True)
    (ws / "modules" / "security-analysis").mkdir(parents=True)
    (ws / "repos" / "web" / "src" / "app.py").write_text(
        "def handler():\n    eval(user_input)  # smell\n", encoding="utf-8"
    )
    (ws / "scripts" / "hello.py").write_text("print('hello from script')\n", encoding="utf-8")
    _seed_agent_docs(ws)
    return ws


def test_tools(ws: Path) -> None:
    tools.set_run_context(ws, "r1")
    check("write_file then read_file round-trips",
          "wrote" in tools.execute_tool(ws, "write_file",
                                        {"path": "runs/r1/m/analysis.md", "content": "hi"})
          and tools.execute_tool(ws, "read_file", {"path": "runs/r1/m/analysis.md"}) == "hi")
    check("list_directory works",
          "web" in tools.execute_tool(ws, "list_directory", {"path": "repos"}))
    check("path escape raises -> ERROR",
          tools.execute_tool(ws, "read_file", {"path": "../../etc/passwd"}).startswith("ERROR"))
    check("missing file -> error string",
          tools.execute_tool(ws, "read_file", {"path": "repos/nope.txt"}) == "ERROR: file not found")
    check("unknown tool -> error string",
          tools.execute_tool(ws, "frobnicate", {}).startswith("ERROR: unknown tool"))
    check("run_script happy path",
          "hello from script" in tools.execute_tool(ws, "run_script", {"script": "hello.py"}))
    check("run_script rejects non-.py",
          tools.execute_tool(ws, "run_script", {"script": "hello.sh"}).startswith("ERROR"))
    check("_find_python resolves", bool(tools._find_python()))
    grep = tools.execute_tool(ws, "grep_repo", {"pattern": "eval"})
    check("grep_repo returns bounded match for 'eval'", "eval" in grep)


def test_schema() -> None:
    good = {"moduleId": "security-analysis", "runId": "r1", "tasks": [
        {"id": "SEC-001", "title": "x", "priority": "high", "effort": "S",
         "evidence": ["repos/web/src/app.py:2"], "rationale": "y", "agentic_spec": "z"}]}
    check("valid tasks.json passes schema", validate_tasks(good) is None)
    bad = {"moduleId": "m", "runId": "r", "tasks": [{"id": "1", "title": "t",
           "priority": "urgent", "effort": "S", "evidence": [], "rationale": "r",
           "agentic_spec": "s"}]}
    check("invalid priority fails schema", validate_tasks(bad) is not None)


def test_write_tasks_validation(ws: Path) -> None:
    tools.set_run_context(ws, "r1")
    bad = json.dumps({"moduleId": "m", "runId": "r1", "tasks": [{"id": "1"}]})
    check("write_tasks rejects invalid schema",
          tools.execute_tool(ws, "write_tasks", {"module_id": "m", "tasks_json": bad})
          .startswith("ERROR"))


def test_run_state(ws: Path) -> None:
    save_run_state(ws, "r1", status="failed", completedModules=["security-analysis"])
    resume = filter_modules(ws, "r1", ["security-analysis", "swot-analysis"],
                            resume=True, force=False)
    check("resume skips completed modules", resume["to_run"] == ["swot-analysis"])
    force = filter_modules(ws, "r1", ["security-analysis", "swot-analysis"],
                           resume=True, force=True)
    check("force reruns all modules",
          force["to_run"] == ["security-analysis", "swot-analysis"])


def test_agents(ws: Path, root: Path) -> None:
    mod_prompt = compose_module_prompt(ws, "RUNTIME_CTX", "# SKILL\nskill marker")
    check("module prompt includes workspace policy",
          "SMOKE_WS_POLICY_MARKER" in mod_prompt)
    check("module prompt includes module-agent role",
          "SMOKE_MOD_ROLE_MARKER" in mod_prompt)
    check("module prompt includes runtime context and SKILL",
          "RUNTIME_CTX" in mod_prompt and "skill marker" in mod_prompt)

    orch_prompt = compose_orchestrator_prompt(ws, "PROJECT_CTX")
    check("orchestrator prompt includes workspace policy",
          "SMOKE_WS_POLICY_MARKER" in orch_prompt)
    check("orchestrator prompt includes project-orchestrator role",
          "SMOKE_PROJ_ROLE_MARKER" in orch_prompt)
    check("orchestrator prompt includes project context",
          "PROJECT_CTX" in orch_prompt)

    bare = root / "bare"
    bare.mkdir()
    fallback = load_agent_doc(bare, "workspace-orchestrator")
    check("missing agents/ uses built-in fallback",
          "fallback" in fallback.lower() or "evidence" in fallback.lower())


def test_intent() -> None:
    ids = ["security-analysis", "accessibility-pass", "swot-analysis"]
    intent = detect_run_intent("run security + accessibility", ids)
    check("intent detects run with modules",
          intent["is_run"] and "security-analysis" in intent["modules"]
          and "accessibility-pass" in intent["modules"])
    rerun = detect_run_intent("re-run dependency health", ["dependency-health"])
    check("intent re-run sets force", rerun["force"] is True)
    qa = detect_run_intent("what did the security module find?", ids)
    check("question is not a run intent", qa["is_run"] is False)


def test_write_json() -> None:
    # success path
    ok = True
    try:
        _write_json({"id": "x", "result": {"ok": True}})
    except SystemExit:
        ok = False
    check("_write_json succeeds to live stdout", ok)


async def test_server_async() -> None:
    srv = SidecarServer()
    health = await srv.handle({"id": "h", "method": "health"})
    check("health before configure -> not ok",
          health["result"]["ok"] is False)
    cfg = await srv.handle({"id": "c", "method": "configure", "params": {"api_key": "fake"}})
    check("configure -> ok", cfg["result"]["ok"] is True)
    unknown = await srv.handle({"id": "u", "method": "frobnicate"})
    check("unknown method -> error", "error" in unknown)

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "ws"
        ws.mkdir()
        (ws / ".contextful.json").write_text(
            '{"display_name":"s","project_type":"both","repos":[{"name":"web","url":"u","branch":"main"}]}',
            encoding="utf-8",
        )
        (ws / "repos" / "web").mkdir(parents=True)
        (ws / "repos" / "web" / "README.md").write_text("# web", encoding="utf-8")
        ws_str = str(ws)

        refresh = await srv.handle({
            "id": "ri", "method": "refresh_index",
            "params": {"workspace": ws_str, "skipEnrichment": True},
        })
        check("refresh_index dispatches (not unknown method)",
              "result" in refresh and "unknown method" not in str(refresh.get("error", "")))
        check("refresh_index creates index file", (ws / ".workspace-index.json").exists())

        preview = await srv.handle({
            "id": "pv", "method": "preview",
            "params": {"workspace": ws_str, "path": "README.md", "base": "repos/web"},
        })
        check("preview dispatches", "result" in preview and preview["result"].get("ok"))


async def test_legacy_projects_async() -> None:
    srv = SidecarServer()
    await srv.handle({"id": "lc", "method": "configure", "params": {"api_key": "fake"}})

    for template_version in LEGACY_PROJECT_VERSIONS:
        with tempfile.TemporaryDirectory() as tmp:
            project = build_legacy_project(Path(tmp), template_version=template_version)
            before = snapshot_text_files(
                project,
                (
                    ".contextful.json",
                    "meta/**",
                    "modules/**",
                    "runs/**",
                    ".eventlog",
                    ".chatlog.json",
                ),
            )
            ws_str = str(project)
            refresh = await srv.handle({
                "id": f"lr-{template_version}",
                "method": "refresh_index",
                "params": {"workspace": ws_str, "skipEnrichment": True},
            })
            label = f"legacy {template_version} refresh_index"
            check(f"{label} dispatches",
                  "result" in refresh and "unknown method" not in str(refresh.get("error", "")))
            check(f"{label} ok", refresh.get("result", {}).get("ok") is True)
            check(f"{label} creates index", (project / INDEX_FILE).exists())
            assert_legacy_files_preserved(project, snapshot=before)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ws = _make_workspace(root)
        test_tools(ws)
        test_schema()
        test_write_tasks_validation(ws)
        test_run_state(ws)
        test_agents(ws, root)
        test_intent()
        test_write_json()
        asyncio.run(test_server_async())
        asyncio.run(test_legacy_projects_async())
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
