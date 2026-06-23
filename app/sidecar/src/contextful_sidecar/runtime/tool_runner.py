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
from contextful_sidecar.runtime.tool_trace import ToolTrace
from contextful_sidecar.runtime.tools import execute_tool
from contextful_sidecar.runtime.transient import is_transient_exception, is_transient_tool_result

EventCallback = Callable[[str, Any], None]
CancelCheck = Callable[[], bool]
ToolExecutor = Callable[..., str]

PROGRESS_INTERVAL_SEC = 6.0
STALL_SEC = 20.0
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


def cap_tool_message(result: str, cap: int = TOOL_RESULT_CAP) -> str:
    """Cap tool output stored in LLM message history (activity keeps full result)."""
    return _cap_result(result, cap)


def _args_summary(args: dict[str, Any]) -> str:
    try:
        text = json.dumps(args)[:160]
    except (TypeError, ValueError):
        text = str(args)[:160]
    return text


async def _run_executor_once(
    executor: ToolExecutor,
    workspace: Path,
    name: str,
    args: dict[str, Any],
    trace: ToolTrace,
) -> tuple[str, bool]:
    """Run tool in thread with timeout. Returns (result, timed_out)."""
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(executor, workspace, name, args, trace),
            timeout=TOOL_TIMEOUT_SEC,
        )
        return result, False
    except TimeoutError:
        summary = trace.summary()
        return f"ERROR: tool timed out after {int(TOOL_TIMEOUT_SEC)}s ({summary})", True
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}", is_transient_exception(exc)


def _traced_execute_tool(
    workspace: Path,
    name: str,
    args: dict[str, Any],
    trace: ToolTrace,
) -> str:
    return execute_tool(workspace, name, args, trace=trace)


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
    tool_executor: ToolExecutor | None = None,
) -> str:
    extra = dict(event_extra or {})
    if on_event:
        payload = {"module": log_scope, "kind": "tool_start", "name": name, "turn": turn, **extra}
        on_event("activity", payload)

    trace = ToolTrace()
    stop_heartbeat = asyncio.Event()
    last_stall_emit = 0.0
    activity_extra = {k: v for k, v in extra.items() if k in ("itemId", "itemIndex", "itemTotal")}

    async def progress_heartbeat() -> None:
        nonlocal last_stall_emit
        while not stop_heartbeat.is_set():
            try:
                await asyncio.wait_for(stop_heartbeat.wait(), timeout=PROGRESS_INTERVAL_SEC)
            except TimeoutError:
                snap = trace.snapshot()
                summary = trace.summary()
                append_eventlog(workspace, log_scope, "TOOL_PROGRESS", f"{name} {summary}")
                if run_id and module_id:
                    append_activity(
                        workspace,
                        run_id,
                        module_id,
                        "tool_progress",
                        turn=turn,
                        name=name,
                        args=args,
                        trace=snap,
                        **activity_extra,
                    )
                if on_event:
                    on_event(
                        "activity",
                        {
                            "module": log_scope,
                            "kind": "tool_progress",
                            "name": name,
                            "turn": turn,
                            "trace": snap,
                            **extra,
                        },
                    )
                    on_event(
                        "heartbeat",
                        {"module": log_scope, "turn": turn, "tool": name, "trace": snap, **extra},
                    )
                idle = time.monotonic() - trace.last_tick_monotonic
                if idle >= STALL_SEC and time.monotonic() - last_stall_emit >= STALL_SEC:
                    last_stall_emit = time.monotonic()
                    append_eventlog(workspace, log_scope, "TOOL_STALL", f"{name} {summary}")
                    if run_id and module_id:
                        append_activity(
                            workspace,
                            run_id,
                            module_id,
                            "tool_stall",
                            turn=turn,
                            name=name,
                            args=args,
                            trace=snap,
                            reason=summary,
                            **activity_extra,
                        )
                    if on_event:
                        on_event(
                            "activity",
                            {
                                "module": log_scope,
                                "kind": "tool_stall",
                                "name": name,
                                "turn": turn,
                                "trace": snap,
                                "reason": summary,
                                **extra,
                            },
                        )

    hb = asyncio.create_task(progress_heartbeat())
    base_executor = tool_executor or _traced_execute_tool

    def executor(ws: Path, tool_name: str, tool_args: dict[str, Any], tool_trace: ToolTrace) -> str:
        if tool_executor is not None:
            # Legacy executors accept (workspace, name, args) only.
            try:
                return tool_executor(ws, tool_name, tool_args, tool_trace)
            except TypeError:
                return tool_executor(ws, tool_name, tool_args)
        return _traced_execute_tool(ws, tool_name, tool_args, tool_trace)

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
            result, timed_out = await _run_executor_once(executor, workspace, name, args, trace)
            transient = (not timed_out) and is_transient_tool_result(result)

            if not transient:
                break
            if attempt >= MAX_TOOL_RETRIES + 1:
                skipped = True
                reason = result
                summary = trace.summary()
                result = (
                    f"ERROR: tool skipped after {attempt} attempts "
                    f"({name} {_args_summary(args)}): {reason[:200]} ({summary})"
                )
                break

            delay = min(
                TOOL_RETRY_BASE_DELAY_SEC * (2 ** (attempt - 1)),
                TOOL_RETRY_MAX_DELAY_SEC,
            )
            retry_line = result.splitlines()[0][:120]
            append_eventlog(
                workspace,
                log_scope,
                "TOOL_RETRY",
                f"{name} attempt {attempt}/{MAX_TOOL_RETRIES + 1} after {delay:.0f}s: "
                f"{retry_line} | {trace.summary()}",
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
                    trace=trace.snapshot(),
                    **activity_extra,
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
                        "trace": trace.snapshot(),
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
            f"{name} {_args_summary(args)} after {attempt - 1} attempts ({duration_ms}ms) | {trace.summary()}",
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
                    "trace": trace.snapshot(),
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
                trace=trace.snapshot(),
                **activity_extra,
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
                "trace": trace.snapshot(),
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
            **activity_extra,
        )
    return result
