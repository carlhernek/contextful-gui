"""Per-module turn-based agent loop (spec section 5)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from contextful_sidecar.runtime.activity import append_activity
from contextful_sidecar.runtime.agents import compose_module_prompt
from contextful_sidecar.runtime.eventlog import append_eventlog
from contextful_sidecar.runtime.openrouter import OpenRouterClient
from contextful_sidecar.runtime.step_log import log_step, logged_chat_completion
from contextful_sidecar.runtime.tool_runner import run_tool_with_liveness
from contextful_sidecar.runtime.tools import (
    TOOL_DEFINITIONS,
    execute_tool,
    set_run_context,
)

EventCallback = Callable[[str, Any], None]

MAX_FETCH_REFUNDS = 20


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
        "write_analysis, write_tasks, grep_repo, run_script, web_search, web_fetch, gather_context.\n"
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
    wrote_analysis = False
    wrote_tasks = False

    def on_token(tok: str) -> None:
        if on_event:
            on_event("token", tok)

    while turn < max_turns:
        turn += 1
        if on_event:
            on_event("turn", {"module": module_id, "turn": turn, "maxTurns": max_turns})
        log_step(
            workspace,
            scope=module_id,
            status="TURN",
            message=f"turn {turn}/{max_turns}",
            run_id=run_id,
            module_id=module_id,
            activity_kind="turn",
            turn=turn,
            maxTurns=max_turns,
        )

        response = await logged_chat_completion(
            workspace=workspace,
            run_id=run_id,
            module_id=module_id,
            scope=module_id,
            client=client,
            model=model,
            messages=messages,
            tools=TOOL_DEFINITIONS,
            on_token=on_token,
        )
        message = response["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []
        messages.append(message)

        content = message.get("content") or ""
        if content.strip():
            append_activity(workspace, run_id, module_id, "thinking", turn=turn, text=content)

        if not tool_calls:
            if not wrote_analysis or not wrote_tasks:
                missing = []
                if not wrote_analysis:
                    missing.append("write_analysis")
                if not wrote_tasks:
                    missing.append("write_tasks")
                messages.append({
                    "role": "user",
                    "content": (
                        "You stopped without required module outputs. "
                        f"Call {' and '.join(missing)} before finishing. "
                        "Read templates/ first. Use tools — do not reply with text only."
                    ),
                })
                continue
            final = content or f"{role} complete."
            append_eventlog(workspace, module_id, "SUCCESS", final.splitlines()[0][:160] if final else "")
            append_activity(workspace, run_id, module_id, "final", turn=turn, text=final)
            return final

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
            log_step(
                workspace,
                scope=module_id,
                status="TOOL",
                message=f"{name} {json.dumps(args)[:160]}",
                run_id=run_id,
                module_id=module_id,
                activity_kind="tool",
                turn=turn,
                name=name,
                args=args,
            )
            if on_event:
                on_event("tool", {"name": name, "args": args})
            result = await run_tool_with_liveness(
                workspace=workspace,
                log_scope=module_id,
                turn=turn,
                name=name,
                args=args,
                on_event=on_event,
                run_id=run_id,
                module_id=module_id,
            )
            results.append(result)
            if name == "write_analysis" and not result.startswith("ERROR:"):
                wrote_analysis = True
            if name == "write_tasks" and not result.startswith("ERROR:"):
                wrote_tasks = True
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id") or name,
                "content": result,
            })

        if _turn_was_only_failed_fetch(tool_calls, results) and fetch_refunds < MAX_FETCH_REFUNDS:
            fetch_refunds += 1
            turn -= 1

    err = f"{role} stopped after {max_turns} turns (incomplete)"
    log_step(
        workspace,
        scope=module_id,
        status="ERROR",
        message=err,
        run_id=run_id,
        module_id=module_id,
        activity_kind="error",
        turn=turn,
    )
    return err
