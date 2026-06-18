"""Shared tool execution with heartbeats and activity/event emission."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable

from contextful_sidecar.runtime.activity import append_activity
from contextful_sidecar.runtime.eventlog import append_eventlog
from contextful_sidecar.runtime.tools import execute_tool

EventCallback = Callable[[str, Any], None]
CancelCheck = Callable[[], bool]

HEARTBEAT_INTERVAL_SEC = 15.0
TOOL_RESULT_CAP = 4000


def _tool_done_summary(name: str, result: str) -> str:
    head = result.splitlines()[0] if result else ""
    if len(head) > 160:
        head = head[:160] + "…"
    return f"{name}: {head}"


def _cap_result(result: str, cap: int = TOOL_RESULT_CAP) -> str:
    if len(result) <= cap:
        return result
    return result[:cap] + "\n...[truncated]"


async def run_tool_with_liveness(
    *,
    workspace: Path,
    log_scope: str,
    turn: int,
    name: str,
    args: dict[str, Any],
    on_event: EventCallback | None = None,
    should_cancel: CancelCheck | None = None,
    run_id: str | None = None,
    module_id: str | None = None,
    event_extra: dict[str, Any] | None = None,
    tool_executor: Callable[[Path, str, dict[str, Any]], str] | None = None,
) -> str:
    extra = dict(event_extra or {})
    if on_event:
        payload = {"module": log_scope, "kind": "tool_start", "name": name, "turn": turn, **extra}
        on_event("activity", payload)

    stop_heartbeat = asyncio.Event()

    async def heartbeat() -> None:
        while not stop_heartbeat.is_set():
            try:
                await asyncio.wait_for(stop_heartbeat.wait(), timeout=HEARTBEAT_INTERVAL_SEC)
            except TimeoutError:
                if on_event:
                    on_event(
                        "heartbeat",
                        {"module": log_scope, "turn": turn, "tool": name, **extra},
                    )

    hb = asyncio.create_task(heartbeat())
    executor = tool_executor or execute_tool
    try:
        result = await asyncio.to_thread(executor, workspace, name, args)
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
        on_event(
            "activity",
            {"module": log_scope, "kind": "tool_done", "name": name, "turn": turn, **extra},
        )
    if run_id and module_id:
        append_activity(
            workspace,
            run_id,
            module_id,
            "tool_result",
            turn=turn,
            name=name,
            result=_cap_result(result),
            **{k: v for k, v in extra.items() if k in ("itemId", "itemIndex", "itemTotal")},
        )
    return result
