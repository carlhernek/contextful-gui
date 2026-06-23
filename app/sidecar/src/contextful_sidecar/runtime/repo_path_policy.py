"""Block sensitive and gitignored paths under repos/ from agent tool context."""
from __future__ import annotations

import fnmatch
import os
import subprocess
import sys
from pathlib import Path

from contextful_sidecar.runtime.tool_trace import get_trace

# Always blocked under repos/ — even if tracked or not listed in .gitignore.
_SECRET_BASENAMES = frozenset({
    ".env",
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".htpasswd",
    "credentials",
    "credentials.json",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "known_hosts",
})
_SECRET_BASENAME_PREFIXES = (".env.",)
_SECRET_SUFFIXES = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".jks",
    ".keystore",
    ".cer",
    ".crt",
    ".der",
)
_SECRET_DIR_SEGMENTS = frozenset({".ssh"})
_SKIP_REPO_SEGMENTS = frozenset({".git"})

# Per-tool-call memo: (repo_root_posix, rel_posix) -> ignored bool | None
_ignore_cache: dict[tuple[str, str], bool | None] = {}
_subproc_count = 0
_subproc_max = 1


def clear_ignore_cache() -> None:
    _ignore_cache.clear()


def reset_subprocess_budget(max_calls: int = 1) -> None:
    global _subproc_count, _subproc_max
    _subproc_count = 0
    _subproc_max = max(1, max_calls)


def _record_git_spawn() -> None:
    global _subproc_count
    _subproc_count += 1
    trace = get_trace()
    if trace:
        trace.tick("subprocs")


def _can_spawn_git() -> bool:
    return _subproc_count < _subproc_max


def _silent_run(*popenargs, **kwargs) -> subprocess.CompletedProcess:
    if sys.platform == "win32":
        kwargs.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
    return subprocess.run(*popenargs, **kwargs)


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "Never"
    env["GIT_PAGER"] = "cat"
    return env


def repo_location(workspace: Path, target: Path) -> tuple[Path, Path] | None:
    """If target is under repos/<name>/, return (repo_root, path relative to repo)."""
    root = workspace.resolve()
    try:
        rel = target.resolve().relative_to(root)
    except ValueError:
        return None
    if not rel.parts or rel.parts[0] != "repos" or len(rel.parts) < 2:
        return None
    repo_root = root / "repos" / rel.parts[1]
    inner = Path(*rel.parts[2:]) if len(rel.parts) > 2 else Path(".")
    return repo_root, inner


def is_under_git_dir(rel_in_repo: Path) -> bool:
    return ".git" in rel_in_repo.parts


def is_sensitive_repo_path(rel_in_repo: Path) -> bool:
    name = rel_in_repo.name
    if not name and rel_in_repo == Path("."):
        return False
    lower = name.lower()
    if lower in _SECRET_BASENAMES:
        return True
    if any(lower.startswith(p) for p in _SECRET_BASENAME_PREFIXES):
        return True
    if any(lower.endswith(s) for s in _SECRET_SUFFIXES):
        return True
    for part in rel_in_repo.parts:
        if part.lower() in _SECRET_DIR_SEGMENTS:
            return True
    return False


def _git_check_ignore(repo_root: Path, rel_in_repo: Path) -> bool | None:
    """Return True if ignored, False if not, None if git unavailable."""
    if is_under_git_dir(rel_in_repo):
        return True
    cache_key = (repo_root.resolve().as_posix(), rel_in_repo.as_posix())
    if cache_key in _ignore_cache and _ignore_cache[cache_key] is not None:
        return _ignore_cache[cache_key]

    git_marker = repo_root / ".git"
    if not git_marker.exists():
        _ignore_cache[cache_key] = None
        return None
    if not _can_spawn_git():
        ignored = _simple_gitignore_match(rel_in_repo, _read_gitignore_patterns(repo_root))
        _ignore_cache[cache_key] = ignored
        return ignored
    rel_str = rel_in_repo.as_posix() if rel_in_repo != Path(".") else "."
    try:
        _record_git_spawn()
        proc = _silent_run(
            ["git", "check-ignore", "-q", "--", rel_str],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
            env=_git_env(),
        )
        result: bool | None = proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        result = None
    _ignore_cache[cache_key] = result
    return result


def batch_git_check_ignore(repo_root: Path, rel_paths: list[Path]) -> dict[Path, bool]:
    """Classify paths as ignored in one git subprocess when possible."""
    out: dict[Path, bool] = {}
    to_check: list[Path] = []
    for rel in rel_paths:
        if is_under_git_dir(rel):
            out[rel] = True
            continue
        cache_key = (repo_root.resolve().as_posix(), rel.as_posix())
        if cache_key in _ignore_cache and _ignore_cache[cache_key] is not None:
            out[rel] = bool(_ignore_cache[cache_key])
            continue
        to_check.append(rel)

    if not to_check:
        return out

    git_marker = repo_root / ".git"
    if not git_marker.exists():
        patterns = _read_gitignore_patterns(repo_root)
        for rel in to_check:
            ignored = _simple_gitignore_match(rel, patterns)
            cache_key = (repo_root.resolve().as_posix(), rel.as_posix())
            _ignore_cache[cache_key] = ignored
            out[rel] = ignored
        return out

    rel_strs = [r.as_posix() if r != Path(".") else "." for r in to_check]
    patterns = _read_gitignore_patterns(repo_root)
    if not _can_spawn_git():
        for rel in to_check:
            ignored = _simple_gitignore_match(rel, patterns)
            cache_key = (repo_root.resolve().as_posix(), rel.as_posix())
            _ignore_cache[cache_key] = ignored
            out[rel] = ignored
        return out
    try:
        _record_git_spawn()
        proc = _silent_run(
            ["git", "check-ignore", "--stdin"],
            cwd=repo_root,
            input="\n".join(rel_strs),
            capture_output=True,
            text=True,
            timeout=30,
            env=_git_env(),
        )
        ignored_set = {line.strip() for line in (proc.stdout or "").splitlines() if line.strip()}
        for rel, rel_str in zip(to_check, rel_strs, strict=True):
            ignored = rel_str in ignored_set
            cache_key = (repo_root.resolve().as_posix(), rel.as_posix())
            _ignore_cache[cache_key] = ignored
            out[rel] = ignored
    except (OSError, subprocess.SubprocessError):
        for rel in to_check:
            if _can_spawn_git():
                checked = _git_check_ignore(repo_root, rel)
                out[rel] = bool(checked) if checked is not None else _simple_gitignore_match(rel, patterns)
            else:
                ignored = _simple_gitignore_match(rel, patterns)
                cache_key = (repo_root.resolve().as_posix(), rel.as_posix())
                _ignore_cache[cache_key] = ignored
                out[rel] = ignored
    return out


def classify_repo_files(
    workspace: Path,
    repo_root: Path,
    files: list[Path],
    dirs: list[Path] | None = None,
) -> set[Path]:
    """Return absolute file paths under workspace that agents may read."""
    repo = repo_root.resolve()
    rel_files: list[Path] = []
    abs_by_rel: dict[Path, Path] = {}
    for fp in files:
        try:
            rel = fp.resolve().relative_to(repo)
        except ValueError:
            continue
        rel_files.append(rel)
        abs_by_rel[rel] = fp.resolve()

    rel_dirs: list[Path] = []
    for dp in dirs or []:
        try:
            rel_dirs.append(dp.resolve().relative_to(repo))
        except ValueError:
            continue

    all_rels = list({*rel_files, *rel_dirs})
    ignored = batch_git_check_ignore(repo, all_rels)

    def rel_ignored(rel: Path) -> bool:
        if is_under_git_dir(rel):
            return True
        if ignored.get(rel, False):
            return True
        for i in range(1, len(rel.parts)):
            parent = Path(*rel.parts[:i])
            if ignored.get(parent, False):
                return True
        return False

    allowed: set[Path] = set()
    for rel, fp in abs_by_rel.items():
        if rel_ignored(rel):
            continue
        if is_sensitive_repo_path(rel):
            continue
        allowed.add(fp)
    return allowed


def ignored_abs_paths(repo_root: Path, abs_paths: list[Path]) -> set[Path]:
    """Return absolute paths that are gitignored or under an ignored ancestor."""
    repo = repo_root.resolve()
    rel_map: dict[Path, Path] = {}
    for fp in abs_paths:
        try:
            rel_map[fp.resolve()] = fp.resolve().relative_to(repo)
        except ValueError:
            continue
    if not rel_map:
        return set()
    ignored = batch_git_check_ignore(repo, list(rel_map.values()))

    def rel_ignored(rel: Path) -> bool:
        if is_under_git_dir(rel):
            return True
        if ignored.get(rel, False):
            return True
        for i in range(1, len(rel.parts)):
            parent = Path(*rel.parts[:i])
            if ignored.get(parent, False):
                return True
        return False

    return {abspath for abspath, rel in rel_map.items() if rel_ignored(rel)}


def _read_gitignore_patterns(repo_root: Path) -> list[str]:
    patterns: list[str] = []
    path = repo_root / ".gitignore"
    if not path.is_file():
        return patterns
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("!"):
                continue
            patterns.append(line)
    except OSError:
        pass
    return patterns


def _simple_gitignore_match(rel_in_repo: Path, patterns: list[str]) -> bool:
    if not patterns:
        return False
    rel_posix = rel_in_repo.as_posix()
    name = rel_in_repo.name
    for pat in patterns:
        if pat.endswith("/"):
            pat_dir = pat.rstrip("/")
            if fnmatch.fnmatch(name, pat_dir) or fnmatch.fnmatch(rel_posix, pat):
                return True
            if any(fnmatch.fnmatch(part, pat_dir) for part in rel_in_repo.parts):
                return True
            continue
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(rel_posix, pat):
            return True
        if "/" in pat and fnmatch.fnmatch(rel_posix, pat):
            return True
    return False


def is_gitignored_repo_path(repo_root: Path, rel_in_repo: Path) -> bool:
    if rel_in_repo == Path("."):
        return False
    if is_under_git_dir(rel_in_repo):
        return True
    cache_key = (repo_root.resolve().as_posix(), rel_in_repo.as_posix())
    if cache_key in _ignore_cache and _ignore_cache[cache_key] is not None:
        return bool(_ignore_cache[cache_key])
    checked = _git_check_ignore(repo_root, rel_in_repo)
    if checked is not None:
        return checked
    return _simple_gitignore_match(rel_in_repo, _read_gitignore_patterns(repo_root))


def check_repo_path(workspace: Path, target: Path) -> str | None:
    """
    Return an ERROR message if agents must not read this path, else None.
    Only applies to paths under repos/<name>/.
    """
    loc = repo_location(workspace, target)
    if loc is None:
        return None
    repo_root, rel_in_repo = loc
    if is_under_git_dir(rel_in_repo):
        return "ERROR: path blocked (.git metadata — never exposed to agents)"
    if is_sensitive_repo_path(rel_in_repo):
        return "ERROR: path blocked (sensitive file — never exposed to agents)"
    if is_gitignored_repo_path(repo_root, rel_in_repo):
        return "ERROR: path blocked (gitignored in target repository)"
    return None


def filter_repo_children(workspace: Path, directory: Path) -> list[Path]:
    """Return children of directory that are allowed for listing under repos/."""
    try:
        children = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError:
        return []
    if repo_location(workspace, directory) is None:
        return children
    return [
        c for c in children
        if c.name not in _SKIP_REPO_SEGMENTS and check_repo_path(workspace, c) is None
    ]
