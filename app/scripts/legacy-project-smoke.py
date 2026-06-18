#!/usr/bin/env python3
"""Smoke: legacy pre-index projects (v1.0.0, v1.1.0) remain compatible with indexing."""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

SIDECAR_SRC = Path(__file__).resolve().parents[1] / "sidecar" / "src"
if str(SIDECAR_SRC) not in sys.path:
    sys.path.insert(0, str(SIDECAR_SRC))

TESTS = Path(__file__).resolve().parents[1] / "sidecar" / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from contextful_sidecar.runtime.indexing import INDEX_FILE, refresh_index  # noqa: E402
from legacy_fixture import (  # noqa: E402
    LEGACY_PROJECT_VERSIONS,
    assert_legacy_files_preserved,
    build_legacy_project,
    snapshot_text_files,
)


async def _check_version(template_version: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = build_legacy_project(Path(tmp), template_version=template_version)
        before = snapshot_text_files(
            project,
            (".contextful.json", "meta/**", "modules/**", "runs/**", ".eventlog", ".chatlog.json"),
        )
        result = await refresh_index(workspace=project, client=None, skip_enrichment=True)
        if not result.get("ok"):
            raise AssertionError(f"refresh_index failed on legacy {template_version}: {result}")
        if not (project / INDEX_FILE).exists():
            raise AssertionError(f"index not created for legacy {template_version}")
        assert_legacy_files_preserved(project, snapshot=before)
        print(f"OK: legacy project {template_version}")


def main() -> int:
    for version in LEGACY_PROJECT_VERSIONS:
        asyncio.run(_check_version(version))
    print("OK: all legacy project versions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
