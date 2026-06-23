"""Tool results in agent message history are capped for LLM context."""
from __future__ import annotations

from contextful_sidecar.runtime.tool_runner import TOOL_RESULT_CAP, cap_tool_message


def test_cap_tool_message_truncates_large_grep():
    huge = "x" * 50_000
    capped = cap_tool_message(huge)
    assert len(capped) <= TOOL_RESULT_CAP + 32
    assert capped.endswith("...[truncated]")


def test_format_exception_empty_message():
    from contextful_sidecar.runtime.runs import format_exception

    class QuietError(Exception):
        pass

    assert "QuietError" in format_exception(QuietError())
