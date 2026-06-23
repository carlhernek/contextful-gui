"""grep_repo noise filtering and read_file line ranges."""
from __future__ import annotations

from pathlib import Path

from contextful_sidecar.runtime.tools import execute_tool


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
