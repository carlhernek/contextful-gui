"""Bounded repository tree walk for gather_context and path policy batching."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

SKIP_DIR_NAMES = frozenset({
    ".git", "node_modules", "target", "dist", "build", "bin", "obj",
    "packages", "vendor", "__pycache__", ".venv", "venv",
})

GATHER_WALK_MAX_ENTRIES = 2000
GATHER_WALK_MAX_DEPTH = 12


def should_skip_dir(name: str) -> bool:
    return name in SKIP_DIR_NAMES or name.startswith(".")


def bounded_walk(
    root: Path,
    *,
    max_entries: int = GATHER_WALK_MAX_ENTRIES,
    max_depth: int = GATHER_WALK_MAX_DEPTH,
    include_file: Callable[[Path], bool] | None = None,
) -> list[Path]:
    """Depth-first walk returning file paths; skips heavy dirs; caps total entries."""
    if not root.is_dir():
        return [root] if root.is_file() else []
    found: list[Path] = []

    def walk(base: Path, depth: int) -> None:
        if len(found) >= max_entries or depth > max_depth:
            return
        try:
            children = sorted(base.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return
        for child in children:
            if len(found) >= max_entries:
                return
            if child.is_dir():
                if should_skip_dir(child.name):
                    continue
                walk(child, depth + 1)
            elif child.is_file():
                if include_file is None or include_file(child):
                    found.append(child)

    walk(root, 0)
    return found
