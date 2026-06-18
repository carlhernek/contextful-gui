"""Orchestrator chat & run-intent detection (spec section 9.2)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from contextful_sidecar.runtime.agents import compose_orchestrator_prompt
from contextful_sidecar.runtime.eventlog import read_eventlog_tail
from contextful_sidecar.runtime.openrouter import OpenRouterClient

EventCallback = Callable[[str, Any], None]
CancelCheck = Callable[[], bool]

# Verbs that signal the user wants to trigger a run.
_RUN_INTENT_RE = re.compile(
    r"\b(re-?run|run|execute|start|kick off|trigger|analyze)\b", re.IGNORECASE
)


def _available_module_ids(workspace: Path) -> list[str]:
    modules = Path(workspace) / "modules"
    if not modules.exists():
        return []
    return sorted(d.name for d in modules.iterdir() if d.is_dir())


def detect_run_intent(message: str, module_ids: list[str]) -> dict[str, Any]:
    """Return {'is_run': bool, 'modules': [...], 'force': bool}.

    Matches module ids and a few friendly aliases against the message when a run verb
    is present. 'force'/'re-run' implies force=True.
    """
    text = message.lower()
    has_verb = bool(_RUN_INTENT_RE.search(text))
    if not has_verb:
        return {"is_run": False, "modules": [], "force": False}

    matched: list[str] = []
    for mid in module_ids:
        words = mid.split("-")
        # Match either the full id, the hyphen-free form, or the dominant keyword.
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
    module_ids = _available_module_ids(ws)

    intent = detect_run_intent(message, module_ids)
    if intent["is_run"]:
        # The Rust layer triggers the actual run; the chat reply just confirms intent.
        return {
            "type": "run_intent",
            "modules": intent["modules"],
            "force": intent["force"],
            "reply": (
                f"Detected a request to run: {', '.join(intent['modules'])}"
                + (" (forced re-run)." if intent["force"] else ".")
            ),
        }

    # Q&A: ground the reply in the modules index + event-log tail + run summaries.
    context = (
        "Available modules:\n  " + "\n  ".join(module_ids) + "\n\n"
        "Recent runs:\n" + _summarize_results(ws) + "\n\n"
        "Recent event log:\n" + "\n".join(read_eventlog_tail(ws, 40))
    )
    system_prompt = compose_orchestrator_prompt(ws, context)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": message},
    ]

    def on_token(tok: str) -> None:
        on_event("token", tok)

    model = models.get("orchestrator") or models.get("module") or models["module"]
    response = await client.chat_completion(model=model, messages=messages, on_token=on_token)
    reply = response["choices"][0]["message"].get("content", "")
    return {"type": "chat", "reply": reply}
