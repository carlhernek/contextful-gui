"""Shared tool execution with heartbeats, timeout, retry, and skip-on-exhaustion."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Callable

from contextful_sidecar.runtime.activity import append_activity
from contextful_sidecar.runtime.eventlog import append_eventlog
from contextful_sidecar.runtime.tool_skips import append_skip
from contextful_sidecar.runtime.tools import execute_tool
from contextful_sidecar.runtime.transient import is_transient_exception, is_transient_tool_result

EventCallback = Callable[[str, Any], None]
CancelCheck = Callable[[], bool]

HEARTBEAT_INTERVAL_SEC = 15.0
TOOL_RESULT_CAP = 4000
TOOL_TIMEOUT_SEC = 90.0
MAX_TOOL_RETRIES = 2
TOOL_RETRY_BASE_DELAY_SEC = 1.0
TOOL_RETRY_MAX_DELAY_SEC = 8.0


def _tool_done_summary(name: str, result: str, duration_ms: int) -> str:
    head = result.splitlines()[0] if result else ""
    if len(head) > 120:
        head = head[:120] + "…"
    size = len(result.encode("utf-8", errors="replace"))
    return f"{name}: {head} ({size} bytes, {duration_ms}ms)"


def _cap_result(result: str, cap: int = TOOL_RESULT_CAP) -> str:
    if len(result) <= cap:
        return result
    return result[:cap] + "\n...[truncated]"


def _args_summary(args: dict[str, Any]) -> str:
    try:
        text = json.dumps(args)[:160]
    except (TypeError, ValueError):
        text = str(args)[:160]
    return text


async def _run_executor_once(
    executor: Callable[[Path, str, dict[str, Any]], str],
    workspace: Path,
    name: str,
    args: dict[str, Any],
) -> tuple[str, bool]:
    """Run tool in thread with timeout. Returns (result, timed_out)."""
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(executor, workspace, name, args),
            timeout=TOOL_TIMEOUT_SEC,
        )
        return result, False
    except TimeoutError:
        return f"ERROR: tool timed out after {int(TOOL_TIMEOUT_SEC)}s", True
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}", is_transient_exception(exc)


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
    t0 = time.monotonic()
    result = ""
    attempt = 0
    skipped = False

    try:
        while True:
            if should_cancel and should_cancel():
                result = "ERROR: cancelled"
                break
            attempt += 1
            result, timed_out = await _run_executor_once(executor, workspace, name, args)
            transient = timed_out or is_transient_tool_result(result)

            if not transient:
                break
            if attempt >= MAX_TOOL_RETRIES + 1:
                skipped = True
                reason = result
                result = (
                    f"ERROR: tool skipped after {attempt} attempts "
                    f"({name} {_args_summary(args)}): {reason[:200]}"
                )
                break

            delay = min(
                TOOL_RETRY_BASE_DELAY_SEC * (2 ** (attempt - 1)),
                TOOL_RETRY_MAX_DELAY_SEC,
            )
            append_eventlog(
                workspace,
                log_scope,
                "TOOL_RETRY",
                f"{name} attempt {attempt}/{MAX_TOOL_RETRIES + 1} after {delay:.0f}s: "
                f"{result.splitlines()[0][:120]}",
            )
            if run_id and module_id:
                append_activity(
                    workspace,
                    run_id,
                    module_id,
                    "tool_retry",
                    turn=turn,
                    name=name,
                    args=args,
                    attempt=attempt,
                    maxAttempts=MAX_TOOL_RETRIES + 1,
                    reason=result.splitlines()[0][:300],
                    **{k: v for k, v in extra.items() if k in ("itemId", "itemIndex", "itemTotal")},
                )
            if on_event:
                on_event(
                    "activity",
                    {
                        "module": log_scope,
                        "kind": "tool_retry",
                        "name": name,
                        "turn": turn,
                        "attempt": attempt,
                        "maxAttempts": MAX_TOOL_RETRIES + 1,
                        **extra,
                    },
                )
            await asyncio.sleep(delay)
    finally:
        duration_ms = int((time.monotonic() - t0) * 1000)
        stop_heartbeat.set()
        hb.cancel()
        try:
            await hb
        except asyncio.CancelledError:
            pass

    if skipped and run_id and module_id:
        append_skip(
            workspace,
            run_id,
            module_id,
            name=name,
            args=args,
            attempts=attempt,
            reason=result,
            duration_ms=duration_ms,
        )
        append_eventlog(
            workspace,
            log_scope,
            "TOOL_SKIP",
            f"{name} {_args_summary(args)} after {attempt - 1} attempts ({duration_ms}ms)",
        )
        if on_event:
            on_event(
                "activity",
                {
                    "module": log_scope,
                    "kind": "tool_skip",
                    "name": name,
                    "turn": turn,
                    "attempts": attempt - 1,
                    "durationMs": duration_ms,
                    "reason": result[:300],
                    **extra,
                },
            )
        if run_id and module_id:
            append_activity(
                workspace,
                run_id,
                module_id,
                "tool_skip",
                turn=turn,
                name=name,
                args=args,
                attempts=attempt,
                durationMs=duration_ms,
                reason=result[:500],
                **{k: v for k, v in extra.items() if k in ("itemId", "itemIndex", "itemTotal")},
            )

    status = "TOOL_SKIP" if skipped else "TOOL_DONE"
    append_eventlog(
        workspace,
        log_scope,
        status,
        _tool_done_summary(name, result, duration_ms),
    )
    if on_event:
        on_event(
            "activity",
            {
                "module": log_scope,
                "kind": "tool_done" if not skipped else "tool_skip",
                "name": name,
                "turn": turn,
                "durationMs": duration_ms,
                "skipped": skipped,
                **extra,
            },
        )
    if run_id and module_id and not skipped:
        append_activity(
            workspace,
            run_id,
            module_id,
            "tool_result",
            turn=turn,
            name=name,
            result=_cap_result(result),
            durationMs=duration_ms,
            resultBytes=len(result.encode("utf-8", errors="replace")),
            **{k: v for k, v in extra.items() if k in ("itemId", "itemIndex", "itemTotal")},
        )
    return result
