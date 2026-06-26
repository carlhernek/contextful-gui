"""Shared timeout+retry guard: passthrough, timeout, transient retry, cancel."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from contextful_sidecar.runtime import guard
from contextful_sidecar.runtime.guard import run_guarded


def _read_eventlog(ws: Path) -> str:
    log = ws / ".eventlog"
    return log.read_text(encoding="utf-8") if log.exists() else ""


def test_success_passthrough_no_logging(tmp_path: Path):
    ws = tmp_path / "project"
    ws.mkdir()

    async def ok():
        return "value"

    result = asyncio.run(
        run_guarded(ok, label="unit", scope="test", workspace=ws)
    )
    assert result == "value"
    # A clean success emits no TIMEOUT/RETRY noise.
    log = _read_eventlog(ws)
    assert "TIMEOUT" not in log
    assert "RETRY" not in log


def test_times_out_then_raises_after_attempts(tmp_path: Path, monkeypatch):
    ws = tmp_path / "project"
    ws.mkdir()
    monkeypatch.setattr(guard, "GUARD_RETRY_BASE_DELAY_SEC", 0.0)
    monkeypatch.setattr(guard, "GUARD_RETRY_MAX_DELAY_SEC", 0.0)
    attempts = 0

    async def hangs():
        nonlocal attempts
        attempts += 1
        await asyncio.sleep(10)

    with pytest.raises(RuntimeError, match="timed out"):
        asyncio.run(
            run_guarded(
                hangs,
                label="hang",
                scope="test",
                workspace=ws,
                timeout_sec=0.05,
                retries=2,
            )
        )
    assert attempts == 3  # initial + 2 retries
    log = _read_eventlog(ws)
    assert "TIMEOUT" in log
    assert "RETRY" in log


def test_transient_error_retried_then_succeeds(tmp_path: Path, monkeypatch):
    ws = tmp_path / "project"
    ws.mkdir()
    monkeypatch.setattr(guard, "GUARD_RETRY_BASE_DELAY_SEC", 0.0)
    monkeypatch.setattr(guard, "GUARD_RETRY_MAX_DELAY_SEC", 0.0)
    calls = 0

    async def flaky():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("connection reset by peer")
        return "recovered"

    result = asyncio.run(
        run_guarded(flaky, label="flaky", scope="test", workspace=ws, retries=2)
    )
    assert result == "recovered"
    assert calls == 2


def test_deterministic_error_raises_immediately(tmp_path: Path):
    ws = tmp_path / "project"
    ws.mkdir()
    calls = 0

    async def boom():
        nonlocal calls
        calls += 1
        raise ValueError("bad input, not transient")

    with pytest.raises(ValueError):
        asyncio.run(
            run_guarded(boom, label="boom", scope="test", workspace=ws, retries=2)
        )
    assert calls == 1  # no retries for a deterministic error


def test_cancelled_error_reraised(tmp_path: Path):
    ws = tmp_path / "project"
    ws.mkdir()

    async def cancel_me():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            run_guarded(cancel_me, label="cancel", scope="test", workspace=ws)
        )


def test_heartbeat_fires_on_timeout(tmp_path: Path, monkeypatch):
    ws = tmp_path / "project"
    ws.mkdir()
    monkeypatch.setattr(guard, "GUARD_RETRY_BASE_DELAY_SEC", 0.0)
    monkeypatch.setattr(guard, "GUARD_RETRY_MAX_DELAY_SEC", 0.0)
    beats: list[str] = []

    async def hangs():
        await asyncio.sleep(10)

    with pytest.raises(RuntimeError):
        asyncio.run(
            run_guarded(
                hangs,
                label="hb",
                scope="test",
                workspace=ws,
                timeout_sec=0.05,
                retries=1,
                heartbeat=beats.append,
            )
        )
    # Heartbeat surfaces liveness to the outer watchdog during retries.
    assert beats


def test_workspace_none_still_guards(monkeypatch):
    monkeypatch.setattr(guard, "GUARD_RETRY_BASE_DELAY_SEC", 0.0)
    monkeypatch.setattr(guard, "GUARD_RETRY_MAX_DELAY_SEC", 0.0)

    async def hangs():
        await asyncio.sleep(10)

    # No workspace -> logging is skipped but the timeout still fires.
    with pytest.raises(RuntimeError, match="timed out"):
        asyncio.run(
            run_guarded(
                hangs,
                label="nows",
                scope="test",
                workspace=None,
                timeout_sec=0.05,
                retries=0,
            )
        )
