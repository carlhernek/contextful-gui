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
    skip_dir: Callable[[Path], bool] | None = None,
    on_dir: Callable[[Path], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[Path]:
    """Depth-first walk returning file paths; skips heavy dirs; caps total entries."""
    if not root.is_dir():
        return [root] if root.is_file() else []
    found: list[Path] = []

    def walk(base: Path, depth: int) -> None:
        if should_stop and should_stop():
            return
        if len(found) >= max_entries or depth > max_depth:
            return
        try:
            children = sorted(base.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return
        for child in children:
            if should_stop and should_stop():
                return
            if len(found) >= max_entries:
                return
            if child.is_dir():
                if should_skip_dir(child.name):
                    continue
                if skip_dir and skip_dir(child):
                    continue
                if on_dir:
                    on_dir(child)
                walk(child, depth + 1)
            elif child.is_file():
                if include_file is None or include_file(child):
                    found.append(child)

    walk(root, 0)
    return found


def collect_dirs_under(root: Path, max_depth: int = GATHER_WALK_MAX_DEPTH) -> list[Path]:
    """Collect directory paths under root (for batch gitignore classification)."""
    if not root.is_dir():
        return []
    dirs: list[Path] = []

    def walk(base: Path, depth: int) -> None:
        if depth > max_depth:
            return
        dirs.append(base)
        try:
            children = sorted(base.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return
        for child in children:
            if child.is_dir() and not should_skip_dir(child.name):
                walk(child, depth + 1)

    walk(root, 0)
    return dirs
