"""Per-module turn-based agent loop (spec section 5)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from contextful_sidecar.runtime.activity import append_activity
from contextful_sidecar.runtime.agent_state import (
    clear_agent_state,
    load_agent_state,
    log_resume,
    save_agent_state,
)
from contextful_sidecar.runtime.agents import compose_module_prompt
from contextful_sidecar.runtime.eventlog import append_eventlog
from contextful_sidecar.runtime.guard import run_guarded
from contextful_sidecar.runtime.openrouter import OpenRouterClient
from contextful_sidecar.runtime.step_log import log_step, logged_chat_completion
from contextful_sidecar.runtime.tool_runner import cap_tool_message, run_tool_with_liveness
from contextful_sidecar.runtime.tools import (
    TOOL_DEFINITIONS,
    execute_tool,
    set_run_context,
)

EventCallback = Callable[[str, Any], None]

MAX_FETCH_REFUNDS = 20

# --- stuck / no-progress detection ----------------------------------------
# A turn counts as unproductive when it repeats a previous turn's exact tool
# calls, re-issues a call that already failed, or produces only errors. After
# STUCK_NUDGE_AT such turns we inject a strong corrective message; if it keeps
# going to STUCK_ABORT_AT we abort so the module can be retried fresh.
STUCK_NUDGE_AT = 3
STUCK_ABORT_AT = 6

# Per-turn LLM timeout: convert a hung/stalled completion into a retry instead
# of an indefinite spin. The client also retries transient HTTP internally.
LLM_TURN_TIMEOUT_SEC = 180.0
LLM_TURN_RETRIES = 2


def _turn_was_only_failed_fetch(tool_calls, results) -> bool:
    if not tool_calls:
        return False
    for call, result in zip(tool_calls, results, strict=True):
        name = (call.get("function") or {}).get("name", "")
        if name not in ("web_fetch", "web_search") or not result.startswith("ERROR:"):
            return False
    return True


def _call_sig(call: dict[str, Any]) -> str:
    fn = call.get("function") or {}
    return f"{fn.get('name', '')}:{fn.get('arguments') or '{}'}"


def _turn_sig(tool_calls: list[dict[str, Any]]) -> str:
    return "|".join(sorted(_call_sig(c) for c in tool_calls))


async def _chat_with_turn_timeout(workspace, run_id, module_id, **kwargs) -> dict[str, Any]:
    """Run one LLM turn under the shared wall-clock timeout+retry guard.

    Raises a transient-flavoured RuntimeError if every attempt times out, so the
    module-level retry loop picks it up instead of the agent spinning forever.
    """
    return await run_guarded(
        lambda: logged_chat_completion(
            workspace=workspace, run_id=run_id, module_id=module_id, **kwargs
        ),
        label=f"{module_id} LLM turn",
        scope=module_id,
        workspace=workspace,
        timeout_sec=LLM_TURN_TIMEOUT_SEC,
        retries=LLM_TURN_RETRIES,
        run_id=run_id,
        module_id=module_id,
    )


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
        "write_analysis, write_tasks, grep_repo, run_script, web_search, web_fetch, "
        "gather_context, gather_run_history.\n"
        "You may ONLY write under runs/<runId>/, research/, and the .eventlog. "
        "Start with gather_context per repo; scope grep_repo to source files; "
        "use read_file start_line/end_line for large files.\n"
        "The run ends automatically after write_analysis and write_tasks succeed — do not keep exploring."
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
    resume_checkpoint: bool = False,
) -> str:
    workspace = Path(workspace)
    set_run_context(workspace, run_id)

    skill_text = instruction_file.read_text(encoding="utf-8", errors="replace")
    if specific_instructions:
        skill_text += (
            "\n\n## Specific Instructions (from user)\n" + specific_instructions.strip() + "\n"
        )

    turn = 0
    fetch_refunds = 0
    wrote_analysis = False
    wrote_tasks = False
    messages: list[dict[str, Any]] = []

    checkpoint = load_agent_state(workspace, run_id, module_id) if resume_checkpoint else None
    if checkpoint:
        messages = checkpoint["messages"]
        turn = int(checkpoint.get("turn", 0))
        wrote_analysis = bool(checkpoint.get("wroteAnalysis"))
        wrote_tasks = bool(checkpoint.get("wroteTasks"))
        fetch_refunds = int(checkpoint.get("fetchRefunds", 0))
        log_resume(workspace, module_id, turn, len(messages))
    else:
        # Fresh start: drop any stale checkpoint so we never resume by accident.
        clear_agent_state(workspace, run_id, module_id)
        system_prompt = _build_system_prompt(
            role=role, module_id=module_id, workspace=workspace, repo_paths=repo_paths,
            meta_docs=meta_docs, project_type=project_type, skill_text=skill_text,
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Begin the {role} analysis now."},
        ]

    # Stuck detection state (not persisted — re-derived per process run).
    prev_turn_sig: str | None = None
    failed_call_sigs: set[str] = set()
    stuck_score = 0
    stuck_nudged = False

    def on_token(tok: str) -> None:
        if on_event:
            on_event("token", tok)

    while turn < max_turns:
        turn += 1
        if turn == max_turns - 3 and (not wrote_analysis or not wrote_tasks):
            messages.append({
                "role": "user",
                "content": (
                    f"Turn budget warning: {turn}/{max_turns}. "
                    "Stop exploring and call write_analysis + write_tasks now with grounded findings so far."
                ),
            })
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

        response = await _chat_with_turn_timeout(
            workspace,
            run_id,
            module_id,
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
            clear_agent_state(workspace, run_id, module_id)
            return final

        if on_event:
            on_event("token", "\n\n")

        sig = _turn_sig(tool_calls)
        dup_turn = sig == prev_turn_sig
        repeat_failed = False

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
            call_sig = _call_sig(call)
            if result.startswith("ERROR:"):
                if call_sig in failed_call_sigs:
                    repeat_failed = True
                failed_call_sigs.add(call_sig)
            if name == "write_analysis" and not result.startswith("ERROR:"):
                wrote_analysis = True
            if name == "write_tasks" and not result.startswith("ERROR:"):
                wrote_tasks = True
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id") or name,
                "content": cap_tool_message(result),
            })

        if wrote_analysis and wrote_tasks:
            final = content.strip() or f"{role} complete."
            append_eventlog(
                workspace, module_id, "SUCCESS",
                final.splitlines()[0][:160] if final else "",
            )
            append_activity(workspace, run_id, module_id, "final", turn=turn, text=final)
            clear_agent_state(workspace, run_id, module_id)
            return final

        # --- stuck / no-progress detection -------------------------------
        all_failed = bool(results) and all(r.startswith("ERROR:") for r in results)
        unproductive = dup_turn or repeat_failed or all_failed
        if unproductive:
            stuck_score += 1
        else:
            stuck_score = 0
            stuck_nudged = False
        prev_turn_sig = sig

        if stuck_score >= STUCK_ABORT_AT:
            err = (
                f"{role} made no progress for {stuck_score} consecutive turns "
                "(repeating the same/failing tool calls) (stuck)"
            )
            log_step(
                workspace, scope=module_id, status="STUCK_ABORT", message=err,
                run_id=run_id, module_id=module_id, activity_kind="error", turn=turn,
            )
            return err

        if stuck_score >= STUCK_NUDGE_AT and not stuck_nudged:
            stuck_nudged = True
            log_step(
                workspace, scope=module_id, status="STUCK_NUDGE",
                message=f"no progress for {stuck_score} turns — injecting corrective guidance",
                run_id=run_id, module_id=module_id, activity_kind="turn", turn=turn,
            )
            missing = []
            if not wrote_analysis:
                missing.append("write_analysis")
            if not wrote_tasks:
                missing.append("write_tasks")
            messages.append({
                "role": "user",
                "content": (
                    "STOP. You are repeating the same tool calls and making no progress. "
                    "Do NOT retry a tool call that already failed (e.g. run_script only runs "
                    ".py files; do not re-run shell scripts). Change approach: rely on the "
                    "context you already have and "
                    + (f"call {' and '.join(missing)} now" if missing else "finish now")
                    + " with grounded findings. Use tools — do not reply with text only."
                ),
            })

        if _turn_was_only_failed_fetch(tool_calls, results) and fetch_refunds < MAX_FETCH_REFUNDS:
            fetch_refunds += 1
            turn -= 1

        save_agent_state(
            workspace, run_id, module_id,
            turn=turn, messages=messages,
            wrote_analysis=wrote_analysis, wrote_tasks=wrote_tasks,
            fetch_refunds=fetch_refunds,
        )

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
