"""Unified step logging to .eventlog and activity.jsonl."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from contextful_sidecar.runtime.activity import append_activity
from contextful_sidecar.runtime.eventlog import append_eventlog
from contextful_sidecar.runtime.openrouter import OpenRouterClient

EventCallback = Callable[[str, Any], None]


def log_step(
    workspace: Path,
    *,
    scope: str,
    status: str,
    message: str = "",
    run_id: str | None = None,
    module_id: str | None = None,
    activity_kind: str | None = None,
    **fields: Any,
) -> None:
    """Append one step to the project event log and optional module activity transcript."""
    append_eventlog(workspace, scope, status, message)
    if run_id and module_id:
        kind = activity_kind or status.lower()
        append_activity(workspace, run_id, module_id, kind, text=message, status=status, **fields)


async def logged_chat_completion(
    *,
    workspace: Path,
    run_id: str,
    module_id: str,
    scope: str,
    client: OpenRouterClient,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    on_token: Callable[[str], None] | None = None,
    event_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call OpenRouter with LLM_REQUEST / LLM_RESPONSE step logging."""
    extra = dict(event_extra or {})
    msg_count = len(messages)
    tool_defs = len(tools or [])
    log_step(
        workspace,
        scope=scope,
        status="LLM_REQUEST",
        message=f"model={model} messages={msg_count} toolDefs={tool_defs}",
        run_id=run_id,
        module_id=module_id,
        activity_kind="llm_request",
        model=model,
        messageCount=msg_count,
        toolDefCount=tool_defs,
        **extra,
    )
    t0 = time.monotonic()
    try:
        response = await client.chat_completion(
            model=model,
            messages=messages,
            tools=tools,
            on_token=on_token,
        )
    except Exception as exc:  # noqa: BLE001
        from contextful_sidecar.runtime.runs import format_exception
        err = format_exception(exc)
        log_step(
            workspace,
            scope=scope,
            status="LLM_ERROR",
            message=err,
            run_id=run_id,
            module_id=module_id,
            activity_kind="error",
            **extra,
        )
        raise
    duration_ms = int((time.monotonic() - t0) * 1000)
    choice = response["choices"][0]
    message = choice["message"]
    tool_calls = message.get("tool_calls") or []
    content = message.get("content") or ""
    finish = choice.get("finish_reason")
    if not finish or finish == "unknown":
        finish = "tool_calls" if tool_calls else "stop"
    log_step(
        workspace,
        scope=scope,
        status="LLM_RESPONSE",
        message=(
            f"durationMs={duration_ms} finish={finish} "
            f"toolCalls={len(tool_calls)} contentLen={len(content)}"
        ),
        run_id=run_id,
        module_id=module_id,
        activity_kind="llm_response",
        durationMs=duration_ms,
        finishReason=finish,
        toolCallCount=len(tool_calls),
        contentLength=len(content),
        **extra,
    )
    return response
