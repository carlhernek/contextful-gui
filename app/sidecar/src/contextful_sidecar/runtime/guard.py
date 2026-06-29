"""Shared wall-clock timeout + bounded-retry guard for long-running awaitables.

Every external/long unit of work (an LLM turn, an STT call, a Supabase GET) is
run through ``run_guarded`` so a hung or stalled operation becomes a logged
retry instead of an indefinite freeze. The guard:

- caps each attempt with ``asyncio.wait_for`` (a wall-clock bound that does not
  rely on the underlying client's own timers, which can fail to fire),
- retries a bounded number of times on timeout or transient errors, logging a
  clear ``TIMEOUT`` then ``RETRY`` line each time,
- raises a transient-flavoured ``RuntimeError`` once attempts are exhausted so
  callers can skip-and-continue or fail the unit loudly, and
- always re-raises ``asyncio.CancelledError`` so user Stop stays instant.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable, TypeVar

from contextful_sidecar.runtime.step_log import log_step
from contextful_sidecar.runtime.transient import is_transient_exception

T = TypeVar("T")

# Default per-unit budget. Network / LLM / transcription units use this; tools
# keep their own stricter 90s budget in tool_runner.
GUARD_TIMEOUT_SEC = 180.0
GUARD_RETRIES = 2  # 3 attempts total
GUARD_RETRY_BASE_DELAY_SEC = 1.0
GUARD_RETRY_MAX_DELAY_SEC = 8.0
# Emit a heartbeat this often during a long guarded await so the outer Rust
# watchdog sees liveness even when the HTTP client is blocked on a large upload.
GUARD_HEARTBEAT_SEC = 30.0


async def _await_with_timeout(
    factory: Callable[[], Awaitable[T]],
    *,
    timeout_sec: float,
    label: str,
    heartbeat: Callable[[str], None] | None,
) -> T:
    """``asyncio.wait_for`` with periodic heartbeats and reliable cancellation."""
    task = asyncio.create_task(factory())
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_sec
    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                raise TimeoutError()
            tick = min(GUARD_HEARTBEAT_SEC, remaining)
            done, _ = await asyncio.wait({task}, timeout=tick)
            if done:
                return task.result()
            if heartbeat:
                heartbeat(f"{label} in progress ({int(remaining)}s left)")
    except asyncio.CancelledError:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        raise


async def run_guarded(
    factory: Callable[[], Awaitable[T]],
    *,
    label: str,
    scope: str,
    workspace: Path | None,
    timeout_sec: float = GUARD_TIMEOUT_SEC,
    retries: int = GUARD_RETRIES,
    run_id: str | None = None,
    module_id: str | None = None,
    heartbeat: Callable[[str], None] | None = None,
) -> T:
    """Run ``factory()`` under a wall-clock timeout with bounded retries.

    ``factory`` must return a fresh awaitable on each call so a timed-out or
    failed attempt can be cleanly retried. Raises ``RuntimeError`` if every
    attempt times out, re-raises a non-transient exception immediately, and
    re-raises ``asyncio.CancelledError`` untouched.

    ``workspace`` may be ``None`` (e.g. account-level calls before a project
    exists); logging is skipped in that case but the timeout/retry still apply.

    ``heartbeat`` is an optional best-effort callback invoked on every timeout
    and retry with a short message; callers use it to surface liveness to the
    outer (Rust) watchdog so legitimate retries are not mistaken for a freeze.
    """
    attempts = retries + 1
    last_exc: BaseException | None = None

    def _beat(message: str) -> None:
        if heartbeat is None:
            return
        try:
            heartbeat(message)
        except Exception:  # noqa: BLE001 — heartbeat is best-effort only
            pass

    def _log(status: str, message: str, activity_kind: str) -> None:
        if not workspace:
            return
        log_step(
            workspace,
            scope=scope,
            status=status,
            message=message,
            run_id=run_id,
            module_id=module_id,
            activity_kind=activity_kind,
        )

    for attempt in range(attempts):
        try:
            return await _await_with_timeout(
                factory,
                timeout_sec=timeout_sec,
                label=label,
                heartbeat=heartbeat,
            )
        except asyncio.CancelledError:
            raise
        except (asyncio.TimeoutError, TimeoutError):
            msg = (
                f"{label} timed out after {int(timeout_sec)}s "
                f"(attempt {attempt + 1}/{attempts})"
            )
            _log("TIMEOUT", msg, "error")
            _beat(msg)
            if attempt >= retries:
                raise RuntimeError(
                    f"{label} timed out after {attempts} attempts "
                    f"({int(timeout_sec)}s each)"
                ) from None
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt >= retries or not is_transient_exception(exc):
                raise
            msg = f"{label} transient error (attempt {attempt + 1}/{attempts}): {exc}"
            _log("TIMEOUT", msg, "error")
            _beat(msg)

        delay = min(
            GUARD_RETRY_BASE_DELAY_SEC * (2 ** attempt),
            GUARD_RETRY_MAX_DELAY_SEC,
        )
        retry_msg = f"{label} retrying (attempt {attempt + 2}/{attempts}) after {delay:.0f}s"
        _log("RETRY", retry_msg, "turn")
        _beat(retry_msg)
        await asyncio.sleep(delay)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"{label} failed without a result")
