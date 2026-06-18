"""gather_context tool for index agents."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from contextful_sidecar.runtime.tools import execute_tool


def test_gather_context_surfaces_docs_and_manifests():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        repo = ws / "repos" / "web"
        repo.mkdir(parents=True)
        (repo / "README.md").write_text("# Web App\nA sample service.\n", encoding="utf-8")
        (repo / "package.json").write_text(json.dumps({"name": "web"}), encoding="utf-8")
        (repo / "docs").mkdir()
        (repo / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")

        out = execute_tool(ws, "gather_context", {"path": "repos/web"})
        assert "Web App" in out
        assert "package.json" in out
        assert "docs/guide.md" in out or "guide.md" in out
        assert len(out) <= 25000
