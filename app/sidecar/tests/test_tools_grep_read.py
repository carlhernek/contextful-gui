"""grep_repo noise filtering, path parsing, and read_file line ranges."""
from __future__ import annotations

from pathlib import Path

from contextful_sidecar.runtime.tools import (
    _grep_parse_file_path,
    execute_tool,
    set_run_context,
)


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "project"
    (ws / "repos" / "web" / "src").mkdir(parents=True)
    return ws


def test_grep_skips_lockfile_and_font(tmp_path: Path):
    ws = _ws(tmp_path)
    repo = ws / "repos" / "web"
    (repo / "package-lock.json").write_text(
        '{"packages":{"node_modules/foo":{"version":"1.0.0","integrity":"Bambora"}}}\n',
        encoding="utf-8",
    )
    (repo / "src" / "app.ts").write_text("export const vendor = 'Bambora';\n", encoding="utf-8")
    font_dir = repo / "assets" / "fonts"
    font_dir.mkdir(parents=True)
    (font_dir / "icon.ttf").write_bytes(b"\x00Bambora\xff")

    out = execute_tool(ws, "grep_repo", {"pattern": "Bambora", "repo": "web"})
    assert "app.ts" in out
    assert "package-lock.json" not in out
    assert "icon.ttf" not in out


def test_read_file_line_range(tmp_path: Path):
    ws = _ws(tmp_path)
    path = ws / "repos" / "web" / "src" / "big.ts"
    path.write_text("\n".join(f"line {i}" for i in range(1, 101)), encoding="utf-8")

    out = execute_tool(
        ws,
        "read_file",
        {"path": "repos/web/src/big.ts", "start_line": 10, "end_line": 12},
    )
    assert "lines 10-12 of 100" in out
    assert "line 10" in out
    assert "line 12" in out
    assert "line 13" not in out


def test_grep_parse_windows_path():
    line = r"C:\repos\API\src\main.rs:42:fn main() {"
    fp = _grep_parse_file_path(line)
    assert fp == Path(r"C:\repos\API\src\main.rs")


def test_write_tasks_wraps_tasks_array(tmp_path: Path):
    ws = _ws(tmp_path)
    (ws / "runs" / "run-1" / "mod").mkdir(parents=True)
    set_run_context(ws, "run-1")
    payload = """{
      "tasks": [{
        "id": "T-1", "title": "x", "priority": "high", "effort": "S",
        "evidence": ["repos/web/a.ts:1"], "rationale": "y", "agentic_spec": "z"
      }]
    }"""
    out = execute_tool(ws, "write_tasks", {"module_id": "mod", "tasks_json": payload})
    assert out.startswith("wrote")
    written = (ws / "runs" / "run-1" / "mod" / "tasks.json").read_text(encoding="utf-8")
    assert '"moduleId": "mod"' in written
    assert '"runId": "run-1"' in written
