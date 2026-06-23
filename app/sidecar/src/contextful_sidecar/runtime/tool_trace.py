"""Lightweight per-tool progress trace readable from the event loop during asyncio.to_thread runs."""
from __future__ import annotations

import time
from contextvars import ContextVar, Token
from typing import Any

_active_trace: ContextVar[ToolTrace | None] = ContextVar("tool_trace", default=None)


class ToolTrace:
  """Thread-published progress snapshot for tool_runner heartbeats and timeout messages."""

  def __init__(self) -> None:
    self.phase = "init"
    self.counters: dict[str, int] = {}
    self.last_path = ""
    self.started_monotonic = time.monotonic()
    self.last_tick_monotonic = self.started_monotonic

  def set_phase(self, name: str) -> None:
    self.phase = name
    self.last_tick_monotonic = time.monotonic()

  def tick(self, counter: str, n: int = 1, path: str | None = None) -> None:
    self.counters[counter] = self.counters.get(counter, 0) + n
    if path:
      self.last_path = path
    self.last_tick_monotonic = time.monotonic()

  def snapshot(self) -> dict[str, Any]:
    now = time.monotonic()
    return {
      "phase": self.phase,
      "counters": dict(self.counters),
      "lastPath": self.last_path,
      "idleSec": round(now - self.last_tick_monotonic, 1),
      "elapsedSec": round(now - self.started_monotonic, 1),
    }

  def summary(self) -> str:
    parts = [f"phase={self.phase}"]
    for key, value in sorted(self.counters.items()):
      parts.append(f"{key}={value}")
    if self.last_path:
      parts.append(f"last={self.last_path}")
    idle = time.monotonic() - self.last_tick_monotonic
    parts.append(f"idle={idle:.0f}s")
    return " ".join(parts)


def get_trace() -> ToolTrace | None:
  return _active_trace.get()


def set_active_trace(trace: ToolTrace | None) -> Token:
  return _active_trace.set(trace)


def reset_active_trace(token: Token) -> None:
  _active_trace.reset(token)
