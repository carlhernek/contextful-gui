"""Legacy project fixtures (pre-index / v1.0–v1.1 layouts) for backward-compat tests."""
from __future__ import annotations

import json
from pathlib import Path

# Template versions shipped before workspace index landed.
LEGACY_PROJECT_VERSIONS = ("1.0.0", "1.1.0")

INDEX_FILES = (
    ".workspace-index.json",
    ".index-annotations.json",
    ".index-cache.json",
)


def build_legacy_project(root: Path, *, template_version: str = "1.0.0") -> Path:
    """Create a pre-index project tree under root and return the project path."""
    if template_version not in LEGACY_PROJECT_VERSIONS:
        raise ValueError(f"unsupported legacy version: {template_version}")

    project = root / "projects" / f"legacy-{template_version.replace('.', '-')}"
    project.mkdir(parents=True)

    (project / "meta").mkdir(parents=True, exist_ok=True)
    (project / "modules" / "security-analysis").mkdir(parents=True, exist_ok=True)
    (project / "runs" / "20250101-ab12" / "security-analysis").mkdir(parents=True, exist_ok=True)

    meta = {
        "display_name": f"Legacy {template_version}",
        "project_type": "both",
        "repos": [
            {"name": "backoffice", "url": "git@example.com/org/backoffice.git", "branch": "develop"},
        ],
    }
    (project / ".contextful.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (project / "meta" / "requirements.md").write_text("# Requirements\n", encoding="utf-8")
    (project / "modules" / "template-version.txt").write_text(f"{template_version}\n", encoding="utf-8")
    (project / "modules" / "security-analysis" / "SKILL.md").write_text(
        "# Security Analysis\n", encoding="utf-8"
    )
    (project / "runs" / "20250101-ab12" / "security-analysis" / "analysis.md").write_text(
        "# Legacy analysis\n", encoding="utf-8"
    )
    (project / ".eventlog").write_text("[2025-01-01T00:00:00Z] gui START\n", encoding="utf-8")

    if template_version == "1.1.0":
        (project / "meta" / "specs").mkdir(parents=True, exist_ok=True)
        (project / "repos" / "backoffice").mkdir(parents=True, exist_ok=True)
        (project / "meta" / "specs" / "api.md").write_text("# API spec\n", encoding="utf-8")
        (project / ".chatlog.json").write_text(
            json.dumps([{"role": "user", "content": "hello", "ts": "2025-01-01T00:00:00Z"}]),
            encoding="utf-8",
        )
        (project / "repos" / "backoffice" / "README.md").write_text("# Backoffice\n", encoding="utf-8")

    for name in INDEX_FILES:
        if (project / name).exists():
            raise AssertionError(f"legacy fixture must not include {name}")

    return project


def assert_legacy_files_preserved(project: Path, *, snapshot: dict[str, str]) -> None:
    """Ensure refresh_index did not mutate pre-existing legacy artefacts."""
    for rel, expected in snapshot.items():
        path = project / rel
        assert path.exists(), f"missing legacy file: {rel}"
        assert path.read_text(encoding="utf-8") == expected, f"legacy file changed: {rel}"


def snapshot_text_files(project: Path, patterns: tuple[str, ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    for pattern in patterns:
        for path in project.glob(pattern):
            if path.is_file():
                out[path.relative_to(project).as_posix()] = path.read_text(encoding="utf-8")
    return out
