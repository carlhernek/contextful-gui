"""Orchestrator chat & run-intent detection (spec section 9.2)."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Callable

from contextful_sidecar.runtime.agents import compose_orchestrator_prompt
from contextful_sidecar.runtime.eventlog import append_eventlog, read_eventlog_tail
from contextful_sidecar.runtime.indexing import format_index_for_prompt, load_index
from contextful_sidecar.runtime.openrouter import OpenRouterClient
from contextful_sidecar.runtime.tools import ORCHESTRATOR_TOOL_DEFINITIONS, execute_readonly_tool

EventCallback = Callable[[str, Any], None]
CancelCheck = Callable[[], bool]

# Verbs that signal the user wants to trigger a run.
_RUN_INTENT_RE = re.compile(
    r"\b(re-?run|run|execute|start|kick off|trigger|analyze)\b", re.IGNORECASE
)

HEARTBEAT_INTERVAL_SEC = 15.0
ORCHESTRATOR_MAX_TURNS = 8


def _available_module_ids(workspace: Path) -> list[str]:
    modules = Path(workspace) / "modules"
    if not modules.exists():
        return []
    return sorted(d.name for d in modules.iterdir() if d.is_dir())


def detect_run_intent(message: str, module_ids: list[str]) -> dict[str, Any]:
    """Return {'is_run': bool, 'modules': [...], 'force': bool}."""
    text = message.lower()
    has_verb = bool(_RUN_INTENT_RE.search(text))
    if not has_verb:
        return {"is_run": False, "modules": [], "force": False}

    matched: list[str] = []
    for mid in module_ids:
        words = mid.split("-")
        candidates = {mid, mid.replace("-", " "), mid.replace("-", "")}
        candidates.update(words)
        if any(c and c in text for c in candidates):
            matched.append(mid)

    force = bool(re.search(r"\bre-?run\b", text))
    is_run = has_verb and bool(matched)
    return {"is_run": is_run, "modules": matched, "force": force}


def _summarize_results(workspace: Path) -> str:
    runs = Path(workspace) / "runs"
    if not runs.exists():
        return "(no runs yet)"
    parts: list[str] = []
    for run_dir in sorted((p for p in runs.iterdir() if p.is_dir()), reverse=True)[:3]:
        mods = [d.name for d in run_dir.iterdir() if d.is_dir()]
        parts.append(f"- run {run_dir.name}: modules {', '.join(mods) or '(none)'}")
    return "\n".join(parts) or "(no runs yet)"


async def _run_readonly_tool(*, workspace, turn, name, args, on_event):
    if on_event:
        on_event("activity", {"scope": "orchestrator", "kind": "tool_start", "name": name, "turn": turn})
    result = await asyncio.to_thread(execute_readonly_tool, workspace, name, args)
    append_eventlog(workspace, "orchestrator", "TOOL_DONE", f"{name}: {result.splitlines()[0][:120]}")
    if on_event:
        on_event("activity", {"scope": "orchestrator", "kind": "tool_done", "name": name, "turn": turn})
    return result


async def _orchestrator_qa(
    *,
    ws: Path,
    message: str,
    client: OpenRouterClient,
    models: dict[str, str],
    on_event: EventCallback,
    should_cancel: CancelCheck,
) -> str:
    index_block = format_index_for_prompt(load_index(ws))
    context = (
        index_block
        + "\nAvailable modules:\n  "
        + "\n  ".join(_available_module_ids(ws))
        + "\n\nRecent runs:\n"
        + _summarize_results(ws)
        + "\n\nRecent event log:\n"
        + "\n".join(read_eventlog_tail(ws, 40))
        + "\n\nYou may use read_file, list_directory, and grep_repo to inspect repos/meta/artefacts."
    )
    system_prompt = compose_orchestrator_prompt(ws, context)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": message},
    ]
    model = models.get("orchestrator") or models.get("module") or models["module"]
    turn = 0

    def on_token(tok: str) -> None:
        on_event("token", tok)

    while turn < ORCHESTRATOR_MAX_TURNS:
        if should_cancel():
            return "Cancelled."
        turn += 1
        on_event("turn", {"scope": "orchestrator", "turn": turn, "maxTurns": ORCHESTRATOR_MAX_TURNS})

        response = await client.chat_completion(
            model=model,
            messages=messages,
            tools=ORCHESTRATOR_TOOL_DEFINITIONS,
            on_token=on_token,
        )
        message_obj = response["choices"][0]["message"]
        tool_calls = message_obj.get("tool_calls") or []
        messages.append(message_obj)

        if not tool_calls:
            return message_obj.get("content", "") or "No response."

        on_event("token", "\n\n")
        for call in tool_calls:
            fn = call.get("function") or {}
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            on_event("tool", {"name": name, "args": args})
            append_eventlog(ws, "orchestrator", "TOOL", f"{name} {json.dumps(args)[:160]}")
            result = await _run_readonly_tool(
                workspace=ws, turn=turn, name=name, args=args, on_event=on_event,
            )
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id") or name,
                "content": result,
            })

    return f"Stopped after {ORCHESTRATOR_MAX_TURNS} turns (incomplete)."


async def handle_chat(
    *,
    workspace: str,
    message: str,
    client: OpenRouterClient,
    models: dict[str, str],
    on_event: EventCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> dict[str, Any]:
    ws = Path(workspace)
    on_event = on_event or (lambda ev, data: None)
    should_cancel = should_cancel or (lambda: False)
    module_ids = _available_module_ids(ws)

    intent = detect_run_intent(message, module_ids)
    if intent["is_run"]:
        return {
            "type": "run_intent",
            "modules": intent["modules"],
            "force": intent["force"],
            "reply": (
                f"Detected a request to run: {', '.join(intent['modules'])}"
                + (" (forced re-run)." if intent["force"] else ".")
            ),
        }

    # Q&A uses the last built index only — no scan/enrich here (Workspace Index module owns that).
    try:
        reply = await _orchestrator_qa(
            ws=ws,
            message=message,
            client=client,
            models=models,
            on_event=on_event,
            should_cancel=should_cancel,
        )
    except Exception as exc:  # noqa: BLE001
        append_eventlog(ws, "orchestrator", "ERROR", f"chat failed — {exc}")
        raise
    return {"type": "chat", "reply": reply}
