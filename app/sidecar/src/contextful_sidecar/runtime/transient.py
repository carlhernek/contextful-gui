"""Shared transient-failure detection for tool and LLM retries."""
from __future__ import annotations

_TRANSIENT_MARKERS = (
    "ssl", "certificate", "timeout", "timed out", "connection",
    "network", "rate limit", "503", "502", "504", "429", "disconnect",
    "temporarily unavailable", "reset by peer",
)

_DETERMINISTIC_PREFIXES = (
    "ERROR: file not found",
    "ERROR: path blocked",
    "ERROR: bad pattern",
    "ERROR: write blocked",
    "ERROR: web_fetch blocked",
    "ERROR: invalid tasks json",
    "ERROR: tasks failed schema validation",
    "ERROR: unknown tool",
    "ERROR: missing argument",
    "ERROR: empty path",
    "ERROR: grep not useful",
    "ERROR: script not found",
    "ERROR: run_script only runs",
    "ERROR: path escapes workspace",
    "ERROR: directory not found",
    "ERROR: path not found",
    "ERROR: search path not found",
)


def is_transient_tool_result(result: str) -> bool:
    if not result.startswith("ERROR:"):
        return False
    lower = result.lower()
    if any(result.startswith(p) for p in _DETERMINISTIC_PREFIXES):
        return False
    return any(m in lower for m in _TRANSIENT_MARKERS)


def is_transient_exception(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(m in msg for m in _TRANSIENT_MARKERS)
