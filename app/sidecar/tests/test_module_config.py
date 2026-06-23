"""Tests for modules/module-config.json resolution."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from contextful_sidecar.runtime.module_config import (
    clear_config_cache,
    get_index_max_turns,
    get_max_turns,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_config_cache()
    yield
    clear_config_cache()


def _write_config(ws: Path, data: dict) -> None:
    path = ws / "modules" / "module-config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_defaults_when_config_missing(tmp_path: Path):
    assert get_max_turns(tmp_path, "security-analysis") == 24
    assert get_index_max_turns(tmp_path) == 8


def test_module_override_and_clamp(tmp_path: Path):
    _write_config(tmp_path, {
        "defaults": {"maxTurns": 24},
        "modules": {
            "accessibility-pass": {"maxTurns": 32},
            "swot-analysis": {"maxTurns": 200},
        },
    })
    assert get_max_turns(tmp_path, "accessibility-pass") == 32
    assert get_max_turns(tmp_path, "swot-analysis") == 64
    assert get_max_turns(tmp_path, "dependency-health") == 24


def test_index_max_turns_per_item(tmp_path: Path):
    _write_config(tmp_path, {
        "defaults": {"maxTurns": 24},
        "modules": {"workspace-index": {"maxTurnsPerItem": 10}},
    })
    assert get_index_max_turns(tmp_path) == 10


def test_resume_bonus_turns(tmp_path: Path):
    _write_config(tmp_path, {
        "defaults": {"maxTurns": 24},
        "modules": {"b2b-low-hanging-features": {"maxTurns": 40}},
    })
    assert get_max_turns(tmp_path, "b2b-low-hanging-features") == 40
    assert get_max_turns(tmp_path, "b2b-low-hanging-features", resume=True) == 56
    assert get_max_turns(tmp_path, "dependency-health", resume=True) == 40


def test_failed_run_state_gets_default_error(tmp_path: Path):
    from contextful_sidecar.runtime.runs import save_run_state, load_run_state

    save_run_state(tmp_path, "r1", status="failed", failedModule="b2b-low-hanging-features", error="")
    state = load_run_state(tmp_path, "r1")
    assert state["error"] == "b2b-low-hanging-features failed"
