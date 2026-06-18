"""Pytest unit invariants for the Contextful sidecar (spec section 15)."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from contextful_sidecar.runtime.agent import _turn_was_only_failed_fetch
from contextful_sidecar.runtime.chat import detect_run_intent
from contextful_sidecar.runtime.runs import filter_modules, load_run_state, save_run_state
from contextful_sidecar.runtime.schema import validate_tasks
from contextful_sidecar import server


# --- _write_json ----------------------------------------------------------
def test_write_json_success(monkeypatch, capsys):
    server._write_json({"id": "x", "result": {"ok": True}})
    out = capsys.readouterr().out
    assert json.loads(out.strip()) == {"id": "x", "result": {"ok": True}}


def test_write_json_broken_stdout_exits_zero(monkeypatch):
    class BrokenStdout(io.StringIO):
        def write(self, *_a, **_k):
            raise BrokenPipeError("pipe closed")

    monkeypatch.setattr(server.sys, "stdout", BrokenStdout())
    with pytest.raises(SystemExit) as exc:
        server._write_json({"id": "x", "result": {}})
    assert exc.value.code == 0


# --- turn refund predicate ------------------------------------------------
def test_turn_refund_only_failed_fetch():
    calls = [{"function": {"name": "web_fetch"}}]
    assert _turn_was_only_failed_fetch(calls, ["ERROR: timeout"]) is True


def test_turn_refund_mixed_not_refunded():
    calls = [{"function": {"name": "web_fetch"}}, {"function": {"name": "read_file"}}]
    assert _turn_was_only_failed_fetch(calls, ["ERROR: timeout", "ok"]) is False


def test_turn_refund_successful_fetch_not_refunded():
    calls = [{"function": {"name": "web_fetch"}}]
    assert _turn_was_only_failed_fetch(calls, ["fetched ..."]) is False


def test_turn_refund_no_calls():
    assert _turn_was_only_failed_fetch([], []) is False


# --- run state transitions ------------------------------------------------
def test_run_state_resume_and_force(tmp_path: Path):
    save_run_state(tmp_path, "r1", status="failed", completedModules=["a"])
    assert load_run_state(tmp_path, "r1")["status"] == "failed"

    resume = filter_modules(tmp_path, "r1", ["a", "b"], resume=True, force=False)
    assert resume["to_run"] == ["b"]

    all_done = filter_modules(tmp_path, "r1", ["a"], resume=True, force=False)
    assert all_done["alreadyComplete"] is True

    forced = filter_modules(tmp_path, "r1", ["a", "b"], resume=True, force=True)
    assert forced["to_run"] == ["a", "b"]
    assert load_run_state(tmp_path, "r1")["completedModules"] == []


# --- tasks schema ---------------------------------------------------------
def _task(**over):
    base = {"id": "SEC-001", "title": "t", "priority": "high", "effort": "S",
            "evidence": ["repos/web/x.ts:1"], "rationale": "r", "agentic_spec": "s"}
    base.update(over)
    return base


def test_schema_valid():
    doc = {"moduleId": "m", "runId": "r", "tasks": [_task()]}
    assert validate_tasks(doc) is None


def test_schema_missing_key():
    assert validate_tasks({"moduleId": "m", "tasks": []}) is not None


def test_schema_bad_effort():
    doc = {"moduleId": "m", "runId": "r", "tasks": [_task(effort="XL")]}
    assert validate_tasks(doc) is not None


def test_schema_evidence_must_be_list():
    doc = {"moduleId": "m", "runId": "r", "tasks": [_task(evidence="repos/x")]}
    assert validate_tasks(doc) is not None


# --- intent detection -----------------------------------------------------
def test_intent_multiple_modules():
    ids = ["security-analysis", "accessibility-pass"]
    intent = detect_run_intent("please run security and accessibility now", ids)
    assert intent["is_run"] is True
    assert set(intent["modules"]) == set(ids)


def test_intent_question_not_run():
    intent = detect_run_intent("what did the security module find?",
                               ["security-analysis"])
    assert intent["is_run"] is False
