"""Per-module turn-based agent loop (spec section 5)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable

from contextful_sidecar.runtime.agents import compose_module_prompt
from contextful_sidecar.runtime.eventlog import append_eventlog
from contextful_sidecar.runtime.openrouter import OpenRouterClient
from contextful_sidecar.runtime.tools import (
    TOOL_DEFINITIONS,
    execute_tool,
    set_run_context,
)

EventCallback = Callable[[str, Any], None]

HEARTBEAT_INTERVAL_SEC = 15.0
MAX_FETCH_REFUNDS = 20


def _tool_done_summary(name: str, result: str) -> str:
    head = result.splitlines()[0] if result else ""
    if len(head) > 160:
        head = head[:160] + "…"
    return f"{name}: {head}"


async def _run_tool_with_liveness(*, workspace, log_scope, turn, name, args, on_event):
    if on_event:
        on_event("activity", {"module": log_scope, "kind": "tool_start", "name": name, "turn": turn})
    stop_heartbeat = asyncio.Event()

    async def heartbeat() -> None:
        while not stop_heartbeat.is_set():
            try:
                await asyncio.wait_for(stop_heartbeat.wait(), timeout=HEARTBEAT_INTERVAL_SEC)
            except TimeoutError:
                if on_event:
                    on_event("heartbeat", {"module": log_scope, "turn": turn, "tool": name})

    hb = asyncio.create_task(heartbeat())
    try:
        # Tools are SYNC (subprocess / file IO). Run off-loop so cancellation
        # and heartbeats keep flowing and the UI never looks frozen.
        result = await asyncio.to_thread(execute_tool, workspace, name, args)
    except Exception as exc:  # noqa: BLE001
        result = f"ERROR: {exc}"
    finally:
        stop_heartbeat.set()
        hb.cancel()
        try:
            await hb
        except asyncio.CancelledError:
            pass
    append_eventlog(workspace, log_scope, "TOOL_DONE", _tool_done_summary(name, result))
    if on_event:
        on_event("activity", {"module": log_scope, "kind": "tool_done", "name": name, "turn": turn})
    return result


def _turn_was_only_failed_fetch(tool_calls, results) -> bool:
    if not tool_calls:
        return False
    for call, result in zip(tool_calls, results, strict=True):
        name = (call.get("function") or {}).get("name", "")
        if name not in ("web_fetch", "web_search") or not result.startswith("ERROR:"):
            return False
    return True


def _build_system_prompt(*, role, module_id, workspace, repo_paths, meta_docs,
                         project_type, skill_text) -> str:
    repos = "\n".join(f"  - repos/{p.name}" for p in repo_paths) or "  (none cloned yet)"
    metas = "\n".join(f"  - meta/{p.name}" for p in meta_docs) or "  (none)"
    runtime_ctx = (
        f"You are the Contextful agent for the '{role}' module (id: {module_id}).\n"
        f"Workspace root: {workspace}\n"
        f"Project type: {project_type}\n"
        "READ-ONLY target repositories (you may read but NEVER write here):\n"
        f"{repos}\n"
        "Meta documents:\n"
        f"{metas}\n\n"
        "You have these tools: read_file, list_directory, write_file, append_eventlog, "
        "write_analysis, write_tasks, grep_repo, run_script, web_search, web_fetch.\n"
        "You may ONLY write under runs/<runId>/, research/, and the .eventlog. Use grep_repo "
        "for code search instead of reading whole large files."
    )
    return compose_module_prompt(workspace, runtime_ctx, skill_text)


async def run_agent(
    *,
    workspace: Path,
    instruction_file: Path,
    model: str,
    client: OpenRouterClient,
    role: str,
    module_id: str,
    run_id: str,
    repo_paths: list[Path],
    meta_docs: list[Path],
    project_type: str,
    specific_instructions: str | None = None,
    on_event: EventCallback | None = None,
    max_turns: int = 24,
) -> str:
    workspace = Path(workspace)
    set_run_context(workspace, run_id)

    skill_text = instruction_file.read_text(encoding="utf-8", errors="replace")
    if specific_instructions:
        skill_text += (
            "\n\n## Specific Instructions (from user)\n" + specific_instructions.strip() + "\n"
        )

    system_prompt = _build_system_prompt(
        role=role, module_id=module_id, workspace=workspace, repo_paths=repo_paths,
        meta_docs=meta_docs, project_type=project_type, skill_text=skill_text,
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Begin the {role} analysis now."},
    ]

    turn = 0
    fetch_refunds = 0

    def on_token(tok: str) -> None:
        if on_event:
            on_event("token", tok)

    while turn < max_turns:
        turn += 1
        if on_event:
            on_event("turn", {"module": module_id, "turn": turn, "maxTurns": max_turns})
        append_eventlog(workspace, module_id, "TURN", f"turn {turn}/{max_turns}")

        response = await client.chat_completion(
            model=model, messages=messages, tools=TOOL_DEFINITIONS, on_token=on_token,
        )
        message = response["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []
        messages.append(message)

        if not tool_calls:
            content = message.get("content", "") or f"{role} complete."
            append_eventlog(workspace, module_id, "SUCCESS", content.splitlines()[0][:160] if content else "")
            return content

        if on_event:
            on_event("token", "\n\n")

        results: list[str] = []
        for call in tool_calls:
            fn = call.get("function") or {}
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            append_eventlog(workspace, module_id, "TOOL", f"{name} {json.dumps(args)[:160]}")
            if on_event:
                on_event("tool", {"name": name, "args": args})
            result = await _run_tool_with_liveness(
                workspace=workspace, log_scope=module_id, turn=turn,
                name=name, args=args, on_event=on_event,
            )
            results.append(result)
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id") or name,
                "content": result,
            })

        if _turn_was_only_failed_fetch(tool_calls, results) and fetch_refunds < MAX_FETCH_REFUNDS:
            fetch_refunds += 1
            turn -= 1  # refund: transient network failures shouldn't burn the budget

    append_eventlog(workspace, module_id, "ERROR", f"stopped after {max_turns} turns")
    return f"{role} stopped after {max_turns} turns (incomplete)"
