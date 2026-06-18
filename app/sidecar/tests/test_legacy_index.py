"""Backward compatibility: pre-index project layouts (v1.0.0, v1.1.0)."""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from contextful_sidecar.runtime.indexing import INDEX_FILE, load_index, refresh_index, scan_items
from legacy_fixture import (
    INDEX_FILES,
    LEGACY_PROJECT_VERSIONS,
    assert_legacy_files_preserved,
    build_legacy_project,
    snapshot_text_files,
)


@pytest.mark.parametrize("template_version", LEGACY_PROJECT_VERSIONS)
def test_legacy_project_has_no_index_files(template_version: str):
    with tempfile.TemporaryDirectory() as tmp:
        project = build_legacy_project(Path(tmp), template_version=template_version)
        for name in INDEX_FILES:
            assert not (project / name).exists()
        assert load_index(project)["items"] == []


@pytest.mark.parametrize("template_version", LEGACY_PROJECT_VERSIONS)
def test_refresh_index_on_legacy_project_is_lazy_and_non_destructive(template_version: str):
    with tempfile.TemporaryDirectory() as tmp:
        project = build_legacy_project(Path(tmp), template_version=template_version)
        before = snapshot_text_files(
            project,
            (
                ".contextful.json",
                "meta/**",
                "modules/**",
                "runs/**",
                ".eventlog",
                ".chatlog.json",
            ),
        )

        async def run():
            result = await refresh_index(
                workspace=project,
                client=None,
                skip_enrichment=True,
            )
            assert result["ok"] is True
            assert result["itemCount"] >= 1

        asyncio.run(run())

        assert (project / INDEX_FILE).exists()
        index = json.loads((project / INDEX_FILE).read_text(encoding="utf-8"))
        assert index["version"] == 1
        assert any(it["id"] == "meta:requirements.md" for it in index["items"])
        if template_version == "1.1.0":
            assert any(it["id"] == "meta:specs/api.md" for it in index["items"])

        assert_legacy_files_preserved(project, snapshot=before)


@pytest.mark.parametrize("template_version", LEGACY_PROJECT_VERSIONS)
def test_scan_items_on_legacy_project(template_version: str):
    with tempfile.TemporaryDirectory() as tmp:
        project = build_legacy_project(Path(tmp), template_version=template_version)
        items = scan_items(project)
        ids = {it["id"] for it in items}
        assert "meta:requirements.md" in ids
        assert "artifact:20250101-ab12/security-analysis/analysis.md" in ids
        if template_version == "1.1.0":
            assert "meta:specs/api.md" in ids
