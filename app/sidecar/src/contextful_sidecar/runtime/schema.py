"""Lightweight validation for tasks.json against templates/tasks.schema.json.

Kept dependency-free (no jsonschema) so the frozen sidecar stays small. Mirrors the
structural constraints of templates/tasks.schema.json in the contextful-files repo.
"""
from __future__ import annotations

from typing import Any

_PRIORITIES = {"high", "medium", "low"}
_EFFORTS = {"S", "M", "L"}
_REQUIRED_TASK_FIELDS = ("id", "title", "priority", "effort", "evidence", "rationale", "agentic_spec")


def validate_tasks(doc: Any) -> str | None:
    """Return None if valid, else a human-readable error string."""
    if not isinstance(doc, dict):
        return "root must be an object"
    for key in ("moduleId", "runId", "tasks"):
        if key not in doc:
            return f"missing required key: {key}"
    if not isinstance(doc["moduleId"], str) or not doc["moduleId"]:
        return "moduleId must be a non-empty string"
    if not isinstance(doc["runId"], str) or not doc["runId"]:
        return "runId must be a non-empty string"
    if not isinstance(doc["tasks"], list):
        return "tasks must be an array"
    for idx, task in enumerate(doc["tasks"]):
        err = _validate_task(task, idx)
        if err:
            return err
    return None


def _validate_task(task: Any, idx: int) -> str | None:
    where = f"tasks[{idx}]"
    if not isinstance(task, dict):
        return f"{where} must be an object"
    for field in _REQUIRED_TASK_FIELDS:
        if field not in task:
            return f"{where} missing required field: {field}"
    if not isinstance(task["id"], str) or not task["id"]:
        return f"{where}.id must be a non-empty string"
    if not isinstance(task["title"], str) or not task["title"]:
        return f"{where}.title must be a non-empty string"
    if task["priority"] not in _PRIORITIES:
        return f"{where}.priority must be one of {sorted(_PRIORITIES)}"
    if task["effort"] not in _EFFORTS:
        return f"{where}.effort must be one of {sorted(_EFFORTS)}"
    if not isinstance(task["evidence"], list) or not all(isinstance(e, str) for e in task["evidence"]):
        return f"{where}.evidence must be an array of strings"
    if not isinstance(task["rationale"], str) or not task["rationale"]:
        return f"{where}.rationale must be a non-empty string"
    if not isinstance(task["agentic_spec"], str) or not task["agentic_spec"]:
        return f"{where}.agentic_spec must be a non-empty string"
    return None
