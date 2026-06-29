"""Tools layer: sandboxed file IO, code search, scripts, and web research (spec section 6)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import fnmatch
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin

import certifi
import httpx

from contextful_sidecar.runtime.file_text import is_binary_path, read_file_as_text, read_text_snippet
from contextful_sidecar.runtime.eventlog import append_eventlog
from contextful_sidecar.runtime.repo_path_policy import (
    check_repo_path,
    classify_repo_files,
    filter_repo_children,
    ignored_abs_paths,
    repo_location,
    reset_subprocess_budget,
)
from contextful_sidecar.runtime.repo_walk import (
    GATHER_WALK_MAX_DEPTH,
    SKIP_DIR_NAMES,
    bounded_walk,
    collect_dirs_under,
)
from contextful_sidecar.runtime.ssrf_guard import validate_fetch_url
from contextful_sidecar.runtime.tool_trace import ToolTrace, get_trace, reset_active_trace, set_active_trace
from contextful_sidecar.runtime.write_policy import check_write_allowed

READ_FILE_CAP = 500_000          # 500KB cap for read_file
WEB_FETCH_CAP = 100_000          # 100k chars for web_fetch
RUN_SCRIPT_TIMEOUT = 120         # seconds
RUN_SCRIPT_OUTPUT_CAP = 8000     # chars
GREP_MAX_MATCHES = 200
GREP_MAX_LINE_LEN = 400
GREP_SOFT_DEADLINE_SEC = 45.0
GATHER_CONTEXT_CAP = 24_000        # chars for gather_context bundle
GATHER_DOC_EXCERPT = 3000          # per doc file excerpt
GATHER_TREE_DEPTH = 2
GATHER_TREE_MAX = 40
GATHER_SOFT_DEADLINE_SEC = 45.0

# Extra ripgrep globs — block common secrets even if tracked in git.
_RG_SECRET_GLOBS = (
    "!.env", "!.env.*", "!.npmrc", "!.pypirc", "!.netrc", "!.htpasswd",
    "!*.pem", "!*.key", "!*.p12", "!*.pfx", "!*.jks", "!*.keystore",
    "!id_rsa", "!id_dsa", "!id_ecdsa", "!id_ed25519",
    "!**/.ssh/**",
)

# Skip lockfiles, fonts, and other paths that waste agent turns on false positives.
_RG_NOISE_GLOBS = (
    "!package-lock.json", "!pnpm-lock.yaml", "!yarn.lock", "!composer.lock",
    "!**/*.ttf", "!**/*.otf", "!**/*.woff", "!**/*.woff2", "!**/*.eot",
    "!**/assets/fonts/**",
)

PROVENANCE_HEADER = (
    "<!-- online research, not original repo material -->\n"
    "<!-- source: {url} -->\n"
)


# --- path sandboxing (mandatory, spec 6.2) --------------------------------
def _resolve(workspace: Path, rel: str) -> Path:
    target = (workspace / rel).resolve()
    root = workspace.resolve()
    if root not in target.parents and target != root:
        raise ValueError(f"path escapes workspace: {rel}")
    return target


# --- subprocess discipline (spec 6.3) -------------------------------------
def _silent_run(*popenargs, **kwargs) -> subprocess.CompletedProcess:
    if sys.platform == "win32":
        kwargs.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
    return subprocess.run(*popenargs, **kwargs)


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"   # never prompt for credentials
    env["GCM_INTERACTIVE"] = "Never"   # disable Git Credential Manager UI
    env["GIT_PAGER"] = "cat"           # never invoke a blocking pager
    return env


def _is_real_python(path: str) -> bool:
    # The Windows Store ships a python3.exe alias that only prints an install
    # message; verify the candidate actually runs and reports Python 3.
    try:
        proc = _silent_run([path, "--version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0 and out.strip().startswith("Python 3")


def _find_python() -> str:
    # A frozen PyInstaller sidecar cannot run arbitrary workspace scripts;
    # resolve a real system interpreter instead.
    for cand in ("python3", "python"):
        found = shutil.which(cand)
        if found and _is_real_python(found):
            return found
    raise RuntimeError("No system Python 3 found for run_script")


# --- OpenAI-style tool schemas --------------------------------------------
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a UTF-8 text file within the workspace (500KB cap). "
                "Use start_line/end_line (1-based, inclusive) to read a slice of large files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "description": "First line to read (1-based)"},
                    "end_line": {"type": "integer", "description": "Last line to read (1-based, inclusive)"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List entries of a directory within the workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "default": "."}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write a UTF-8 text file under the workspace (creates parent dirs).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_eventlog",
            "description": "Append a line to the workspace event log.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string"},
                    "status": {"type": "string"},
                    "message": {"type": "string"},
                },
                "required": ["scope", "status"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_analysis",
            "description": "Write the raw analysis markdown for a module to its run folder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "module_id": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["module_id", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_tasks",
            "description": "Write kanban tasks JSON for a module (validated against the schema).",
            "parameters": {
                "type": "object",
                "properties": {
                    "module_id": {"type": "string"},
                    "tasks_json": {
                        "type": "string",
                        "description": "JSON object string per templates/tasks.schema.json",
                    },
                },
                "required": ["module_id", "tasks_json"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_repo",
            "description": (
                "Bounded ripgrep over read-only target repos. Prefer path (e.g. repos/API/src) "
                "or repo + glob on large codebases; whole-repo greps on Rust/TS monorepos are slow."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "repo": {"type": "string", "description": "Repo folder name under repos/"},
                    "path": {"type": "string", "description": "Workspace-relative search root (e.g. meta/Notes)"},
                    "glob": {"type": "string"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_script",
            "description": "Run a .py helper from scripts/ (120s timeout, output capped).",
            "parameters": {
                "type": "object",
                "properties": {
                    "script": {"type": "string"},
                    "args": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["script"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for online research.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch a URL and save it under research/ with a provenance header.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "filename": {"type": "string"},
                },
                "required": ["url", "filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gather_context",
            "description": (
                "Gather a condensed context bundle for a path: README/docs, API specs, "
                "stack manifests, and a bounded directory tree. For large repos prefer a "
                "subpath (e.g. repos/API/src) rather than the repo root."
            ),
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gather_run_history",
            "description": (
                "Structured run timeline, open tasks from recent runs, and index/meta "
                "delta since the last completed analysis run. Call first in suggested-next-steps."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

INDEX_TOOL_NAMES = frozenset({
    "read_file", "list_directory", "grep_repo", "gather_context",
})
INDEX_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    t for t in TOOL_DEFINITIONS
    if (t.get("function") or {}).get("name") in INDEX_TOOL_NAMES
]

ORCHESTRATOR_READONLY_TOOLS = frozenset({"read_file", "list_directory", "grep_repo"})

ORCHESTRATOR_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    t for t in TOOL_DEFINITIONS
    if (t.get("function") or {}).get("name") in ORCHESTRATOR_READONLY_TOOLS
]


# --- run-folder helpers ---------------------------------------------------
# The agent sets these per run via set_run_context so write_analysis/write_tasks
# know which run folder to target without the model passing the runId every call.
_RUN_ID: dict[str, str] = {}


def _ws_key(workspace: Path) -> str:
    return workspace.resolve().as_posix()


def _run_id_for(workspace: Path) -> str:
    return _RUN_ID.get(_ws_key(workspace), "")


def set_run_context(workspace: Path, run_id: str) -> None:
    _RUN_ID[_ws_key(workspace)] = run_id


# --- individual tools -----------------------------------------------------
def _read_file(
    workspace: Path,
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
    trace: ToolTrace | None = None,
) -> str:
    trace = trace or get_trace()
    if trace:
        trace.set_phase("read")
    target = _resolve(workspace, path)
    if target.is_dir():
        return f"ERROR: '{path}' is a directory; use list_directory"
    if not target.exists():
        return "ERROR: file not found"
    if blocked := check_repo_path(workspace, target):
        return blocked
    text = read_file_as_text(target, cap=READ_FILE_CAP)
    if trace:
        trace.tick("bytes", len(text.encode("utf-8", errors="replace")), path=path)
    if text.startswith("ERROR:") or text.startswith("binary file"):
        return text
    if start_line is not None or end_line is not None:
        lines = text.splitlines()
        if not lines:
            return "(empty file)"
        first = max(1, int(start_line)) if start_line is not None else 1
        last = min(len(lines), int(end_line)) if end_line is not None else len(lines)
        if first > len(lines):
            return f"ERROR: start_line {first} past end of file ({len(lines)} lines)"
        if last < first:
            return f"ERROR: end_line {last} before start_line {first}"
        sliced = lines[first - 1:last]
        header = f"# {path} lines {first}-{last} of {len(lines)}\n"
        return header + "\n".join(sliced)
    return text


def _list_directory(workspace: Path, path: str = ".", trace: ToolTrace | None = None) -> str:
    trace = trace or get_trace()
    if trace:
        trace.set_phase("iterdir")
    target = _resolve(workspace, path)
    if not target.exists():
        return "ERROR: directory not found"
    if not target.is_dir():
        return f"ERROR: '{path}' is not a directory"
    entries = []
    for child in filter_repo_children(workspace, target):
        kind = "dir" if child.is_dir() else "file"
        entries.append(f"{kind}\t{child.name}")
        if trace:
            trace.tick("entries", path=path)
    hint = "(note: a '.git' entry inside a worktree is a pointer file, not a directory)"
    return "\n".join(entries) + f"\n{hint}" if entries else f"(empty)\n{hint}"


def _write_file(workspace: Path, path: str, content: str) -> str:
    if not path or not path.strip():
        return "ERROR: empty path"
    target = _resolve(workspace, path)
    run_id = _run_id_for(workspace)
    if blocked := check_write_allowed(workspace, target, run_id):
        return blocked
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} bytes to {path}"


def _write_analysis(workspace: Path, module_id: str, content: str) -> str:
    run_id = _run_id_for(workspace)
    rel = f"runs/{run_id}/{module_id}/analysis.md"
    return _write_file(workspace, rel, content)


def _write_tasks(workspace: Path, module_id: str, tasks_json: str) -> str:
    run_id = _run_id_for(workspace)
    try:
        parsed = json.loads(tasks_json) if isinstance(tasks_json, str) else tasks_json
    except json.JSONDecodeError as exc:
        return f"ERROR: invalid tasks JSON: {exc}"
    if isinstance(parsed, dict) and "moduleId" not in parsed:
        if "tasks" in parsed and isinstance(parsed["tasks"], list):
            parsed = {
                "moduleId": module_id,
                "runId": run_id or parsed.get("runId", ""),
                "tasks": parsed["tasks"],
            }
        else:
            return "ERROR: tasks JSON must include moduleId, runId, and tasks array"
    from contextful_sidecar.runtime.schema import validate_tasks
    error = validate_tasks(parsed)
    if error:
        return f"ERROR: tasks failed schema validation: {error}"
    rel = f"runs/{run_id}/{module_id}/tasks.json"
    return _write_file(workspace, rel, json.dumps(parsed, indent=2))


def _find_rg() -> str | None:
    found = shutil.which("rg")
    if found:
        return found
    if sys.platform == "win32":
        for cand in (
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ripgrep" / "rg.exe",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "ripgrep" / "rg.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "rg.exe",
        ):
            if cand.is_file():
                return str(cand)
    return None


def _grep_parse_file_path(line: str) -> Path | None:
    """Parse ripgrep line output path (handles Windows drive letters via rsplit)."""
    parts = line.rsplit(":", 2)
    if len(parts) != 3:
        return None
    try:
        int(parts[1])
    except ValueError:
        return None
    return Path(parts[0])


def _grep_repo(workspace: Path, pattern: str, repo: str | None = None,
               glob: str | None = None, path: str | None = None,
               trace: ToolTrace | None = None) -> str:
    trace = trace or get_trace()
    if trace:
        trace.set_phase("rg_spawn")
    if glob and any(glob.lower().endswith(ext) for ext in (
        ".docx", ".doc", ".pdf", ".zip", ".png", ".jpg", ".xlsx", ".pptx",
        ".ttf", ".otf", ".woff", ".woff2", ".eot",
    )):
        return "ERROR: grep not useful on binary files; use read_file for document text"
    if path:
        search_root = _resolve(workspace, path)
    elif repo:
        search_root = _resolve(workspace, f"repos/{repo}")
    else:
        search_root = _resolve(workspace, "repos")
    if not search_root.exists():
        return "ERROR: search path not found"
    if search_root.is_file() and is_binary_path(search_root):
        return "ERROR: grep not useful on binary files; use read_file"
    if blocked := check_repo_path(workspace, search_root):
        return blocked
    rg = _find_rg()
    started = time.monotonic()

    def over_budget() -> bool:
        return time.monotonic() - started > GREP_SOFT_DEADLINE_SEC

    if rg:
        args = [rg, "--line-number", "--no-heading", "--color", "never",
                "--max-count", str(GREP_MAX_MATCHES)]
        if glob:
            args += ["--glob", glob]
        for secret_glob in _RG_SECRET_GLOBS:
            args += ["--glob", secret_glob]
        for noise_glob in _RG_NOISE_GLOBS:
            args += ["--glob", noise_glob]
        args += [pattern, str(search_root)]
        try:
            proc = _silent_run(args, capture_output=True, text=True, timeout=60)
            out = proc.stdout or proc.stderr or "(no matches)"
        except subprocess.TimeoutExpired:
            return "ERROR: ripgrep timed out after 60s; use path to narrow search (e.g. repos/API/src)"
        except OSError as exc:
            return f"ERROR: grep failed: {exc}"
    else:
        if trace:
            trace.set_phase("scan")
        out = _python_grep(workspace, search_root, pattern, glob, trace=trace, over_budget=over_budget)
    if trace:
        trace.set_phase("filter")
        trace.tick("matches", len(out.splitlines()) if out else 0)
    filtered = _filter_grep_output(workspace, out, trace=trace)
    if over_budget() and filtered not in {"(no matches)", "(no output)"}:
        filtered += "\n(partial: soft deadline reached during grep)"
    return _cap_grep_output(filtered)


def _filter_grep_output(workspace: Path, out: str, trace: ToolTrace | None = None) -> str:
    if not out or out in {"(no matches)", "(no output)"}:
        return out
    trace = trace or get_trace()
    if trace:
        trace.set_phase("filter")
    parsed: list[tuple[str, Path]] = []
    for line in out.splitlines():
        fp = _grep_parse_file_path(line)
        if fp is None:
            colon = line.find(":")
            if colon <= 0:
                parsed.append((line, Path()))
                continue
            fp = Path(line[:colon])
        parsed.append((line, fp))

    unique_files: list[Path] = []
    seen: set[str] = set()
    for _, fp in parsed:
        if not fp.parts:
            continue
        key = fp.resolve().as_posix()
        if key not in seen:
            seen.add(key)
            unique_files.append(fp.resolve())

    allowed: set[Path] = set()
    if unique_files:
        loc = repo_location(workspace, unique_files[0])
        if loc:
            repo_root, _ = loc
            allowed = classify_repo_files(workspace, repo_root, unique_files)
        else:
            allowed = set(unique_files)

    kept: list[str] = []
    for line, fp in parsed:
        if not fp.parts:
            kept.append(line)
            continue
        try:
            if fp.resolve() in allowed:
                kept.append(line)
        except OSError:
            continue
        if trace:
            trace.tick("filtered", path=str(fp))
    return "\n".join(kept) if kept else "(no matches)"


def _grep_skip_file(path: Path) -> bool:
    name = path.name.lower()
    if name in {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "composer.lock"}:
        return True
    if is_binary_path(path):
        return True
    parts = {p.lower() for p in path.parts}
    if "fonts" in parts and "assets" in parts:
        return True
    return False


def _python_grep(
    workspace: Path,
    root: Path,
    pattern: str,
    glob: str | None,
    trace: ToolTrace | None = None,
    over_budget: Callable[[], bool] | None = None,
) -> str:
    import re
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return f"ERROR: bad pattern: {exc}"

    def glob_ok(p: Path) -> bool:
        if not glob:
            return True
        return fnmatch.fnmatch(p.name, glob) or fnmatch.fnmatch(p.as_posix(), glob)

    candidates = bounded_walk(
        root,
        include_file=glob_ok,
        should_stop=over_budget,
    )
    allowed: set[Path] = set(candidates)
    loc = repo_location(workspace, root)
    if loc:
        allowed = classify_repo_files(workspace, loc[0], candidates)

    matches: list[str] = []
    for f in sorted(allowed, key=lambda p: p.as_posix().lower()):
        if over_budget and over_budget():
            break
        if _grep_skip_file(f):
            continue
        try:
            for i, line in enumerate(f.open("r", encoding="utf-8", errors="replace"), 1):
                if rx.search(line):
                    matches.append(f"{f}:{i}:{line.rstrip()}")
                    if trace:
                        trace.tick("matches", path=str(f))
                    if len(matches) >= GREP_MAX_MATCHES:
                        return "\n".join(matches)
        except OSError:
            continue
    return "\n".join(matches) if matches else "(no matches)"


def _cap_grep_output(out: str) -> str:
    lines = out.splitlines()[:GREP_MAX_MATCHES]
    capped = [ln[:GREP_MAX_LINE_LEN] + ("…" if len(ln) > GREP_MAX_LINE_LEN else "")
              for ln in lines]
    return "\n".join(capped)


def _run_script(workspace: Path, script: str, args: list[str] | None = None,
                trace: ToolTrace | None = None) -> str:
    trace = trace or get_trace()
    if trace:
        trace.set_phase("exec")
    if not script.endswith(".py"):
        return "ERROR: run_script only runs .py files"
    target = _resolve(workspace, f"scripts/{Path(script).name}")
    if not target.exists():
        return f"ERROR: script not found in scripts/: {script}"
    py = _find_python()
    cmd = [py, str(target), *(args or [])]
    try:
        proc = _silent_run(cmd, capture_output=True, text=True,
                           timeout=RUN_SCRIPT_TIMEOUT, cwd=str(workspace.resolve()))
    except subprocess.TimeoutExpired:
        return "ERROR: script timed out after 120s"
    except OSError as exc:
        return f"ERROR: {exc}"
    out = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
    if len(out) > RUN_SCRIPT_OUTPUT_CAP:
        out = out[:RUN_SCRIPT_OUTPUT_CAP] + "\n...[truncated]"
    return out or "(no output)"


def _web_fetch(workspace: Path, url: str, filename: str, trace: ToolTrace | None = None) -> str:
    trace = trace or get_trace()
    if trace:
        trace.set_phase("connect")
    if blocked := validate_fetch_url(url):
        return blocked
    headers = {"User-Agent": "Mozilla/5.0 (compatible; Contextful/1.0)"}
    try:
        with httpx.Client(timeout=30, follow_redirects=False, verify=certifi.where()) as c:
            r = c.get(url, headers=headers)
            if r.is_redirect and r.headers.get("location"):
                redirect_url = urljoin(str(r.url), r.headers["location"])
                if blocked := validate_fetch_url(redirect_url):
                    return blocked
                r = c.get(redirect_url, headers=headers)
            r.raise_for_status()
            text = r.text
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: fetch failed: {exc}"
    if trace:
        trace.set_phase("download")
        trace.tick("bytes", len(text.encode("utf-8", errors="replace")), path=url)
    if len(text) > WEB_FETCH_CAP:
        text = text[:WEB_FETCH_CAP] + "\n...[truncated]"
    body = PROVENANCE_HEADER.format(url=url) + text
    _write_file(workspace, f"research/{Path(filename).name}", body)
    return f"fetched {url} -> research/{Path(filename).name} ({len(text)} chars)"


def _web_search(workspace: Path, query: str) -> str:
    # OpenRouter-backed models can browse via this hook; without a search provider
    # configured, return a clear, non-fatal message so the agent can adapt.
    return (
        "ERROR: web_search has no provider configured in this build; "
        "use web_fetch with a known URL instead."
    )


def _excerpt_file(path: Path, cap: int = GATHER_DOC_EXCERPT) -> str:
    if not path.is_file():
        return ""
    if is_binary_path(path):
        return read_text_snippet(path, cap=cap)
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(data) > cap:
        return data[:cap] + "\n...[truncated]"
    return data


def _gather_tree_from_allowed(
    workspace: Path,
    tree_root: Path,
    allowed: set[Path],
    trace: ToolTrace | None = None,
) -> list[str]:
    trace = trace or get_trace()
    if trace:
        trace.set_phase("tree")
    lines: list[str] = []
    tree_root = tree_root.resolve()
    ws = workspace.resolve()
    seen: set[str] = set()

    def add_entry(kind: str, rel_posix: str) -> None:
        if rel_posix in seen or len(lines) >= GATHER_TREE_MAX:
            return
        seen.add(rel_posix)
        lines.append(f"{kind}\t{rel_posix}")
        if trace:
            trace.tick("tree_entries", path=rel_posix)

    for fp in sorted(allowed, key=lambda p: p.as_posix().lower()):
        try:
            rel_to_root = fp.relative_to(tree_root)
            rel_ws = fp.relative_to(ws).as_posix()
        except ValueError:
            continue
        if len(rel_to_root.parts) > GATHER_TREE_DEPTH:
            continue
        for i in range(1, len(rel_to_root.parts)):
            if i > GATHER_TREE_DEPTH:
                break
            dir_path = tree_root / Path(*rel_to_root.parts[:i])
            try:
                add_entry("dir", dir_path.relative_to(ws).as_posix())
            except ValueError:
                pass
        add_entry("file", rel_ws)
        if len(lines) >= GATHER_TREE_MAX:
            break
    return lines


def _gather_context(workspace: Path, path: str, trace: ToolTrace | None = None) -> str:
    trace = trace or get_trace()
    started = time.monotonic()

    def over_budget() -> bool:
        return time.monotonic() - started > GATHER_SOFT_DEADLINE_SEC

    if trace:
        trace.set_phase("init")
    target = _resolve(workspace, path)
    if not target.exists():
        return f"ERROR: path not found: {path}"
    if blocked := check_repo_path(workspace, target):
        return blocked

    sections: list[str] = [f"# Context bundle for {path}\n"]
    search_root = target if target.is_dir() else target.parent
    partial_note = ""

    if trace:
        trace.set_phase("walk")
    ignored_dirs: set[Path] = set()
    loc = repo_location(workspace, search_root) if search_root.is_dir() else None
    if loc and not over_budget():
        repo_root, _ = loc
        shallow_dirs = collect_dirs_under(search_root, max_depth=3)
        if shallow_dirs:
            if trace:
                trace.set_phase("prune_dirs")
            ignored_dirs = ignored_abs_paths(repo_root, shallow_dirs)
            ignored_dirs.discard(search_root.resolve())

    def skip_dir(d: Path) -> bool:
        return d in ignored_dirs or any(d.is_relative_to(ig) for ig in ignored_dirs if ig != d)

    all_files = bounded_walk(
        search_root,
        skip_dir=skip_dir if ignored_dirs else None,
        should_stop=over_budget,
        on_dir=(lambda d: trace.tick("dirs", path=d.relative_to(workspace).as_posix()) if trace else None),
    )
    if trace:
        trace.tick("files", len(all_files), path=path)

    allowed_set: set[Path] = set()
    if loc and not over_budget():
        repo_root, _ = loc
        if trace:
            trace.set_phase("classify_ignore")
        walk_dirs = collect_dirs_under(search_root, max_depth=GATHER_WALK_MAX_DEPTH)
        allowed_set = classify_repo_files(workspace, repo_root, all_files, walk_dirs)
        if trace:
            trace.tick("allowed", len(allowed_set))
    else:
        allowed_set = {fp for fp in all_files if check_repo_path(workspace, fp) is None}

    if over_budget():
        partial_note = "\n\n(partial: soft deadline reached during gather)\n"

    allowed_list = sorted(allowed_set, key=lambda p: p.as_posix().lower())

    doc_candidates: list[Path] = []
    if target.is_file() and target in allowed_set:
        doc_candidates.append(target)
    elif target.is_dir():
        for name in ("README.md", "README", "README.txt", "ARCHITECTURE.md", "CONTRIBUTING.md"):
            p = target / name
            if p in allowed_set:
                doc_candidates.append(p)

    api_patterns = ("openapi", "swagger", "graphql")
    doc_suffixes = {".md", ".txt", ".rst"}
    api_suffixes = {".yaml", ".yml", ".json", ".graphql", ".gql"}

    if trace:
        trace.set_phase("docs")
    for fp in allowed_list:
        if over_budget():
            break
        if fp.suffix.lower() in doc_suffixes and fp not in doc_candidates:
            if fp.parent.name == "docs" or fp.name in {
                "README.md", "README", "README.txt", "ARCHITECTURE.md", "CONTRIBUTING.md",
            }:
                doc_candidates.append(fp)
                if trace:
                    trace.tick("docs", path=fp.relative_to(workspace).as_posix())
                if len(doc_candidates) >= 8:
                    break

    api_files: list[str] = []
    if trace:
        trace.set_phase("api_specs")
    for fp in allowed_list:
        if over_budget():
            break
        lower = fp.name.lower()
        if any(p in lower for p in api_patterns) and fp.suffix.lower() in api_suffixes:
            rel = fp.relative_to(workspace).as_posix()
            body = _excerpt_file(fp, 4000)
            api_files.append(f"### {rel}\n{body}\n")
            if trace:
                trace.tick("api_specs", path=rel)
            if len(api_files) >= 4:
                break

    if doc_candidates:
        sections.append("## Documentation\n")
        for fp in doc_candidates[:8]:
            rel = fp.relative_to(workspace).as_posix()
            body = _excerpt_file(fp)
            if body.strip():
                sections.append(f"### {rel}\n{body}\n")

    manifest_names = (
        "package.json", "pyproject.toml", "Cargo.toml", "go.mod",
        "requirements.txt", "composer.json", "pom.xml",
    )
    manifests: list[str] = []
    if trace:
        trace.set_phase("manifests")
    for name in manifest_names:
        fp = search_root / name
        if fp in allowed_set:
            manifests.append(f"### {fp.relative_to(workspace).as_posix()}\n{_excerpt_file(fp, 2000)}\n")
            if trace:
                trace.tick("manifests", path=name)
    if manifests:
        sections.append("## Stack manifests\n" + "\n".join(manifests))

    if api_files:
        sections.append("## API specs\n" + "\n".join(api_files))

    tree_root = target if target.is_dir() else target.parent
    tree_lines = _gather_tree_from_allowed(workspace, tree_root, allowed_set, trace=trace)
    if tree_lines:
        sections.append("## Directory tree (bounded)\n" + "\n".join(tree_lines))

    out = "\n".join(sections) + partial_note
    if len(out) > GATHER_CONTEXT_CAP:
        out = out[:GATHER_CONTEXT_CAP] + "\n...[truncated]"
    return out or f"(no context gathered for {path})"


def _has_analysis_modules(completed_modules: list[Any]) -> bool:
    from contextful_sidecar.runtime.runs import WORKSPACE_INDEX_MODULE

    return any(str(m) != WORKSPACE_INDEX_MODULE for m in completed_modules)


def _mtime_iso(fp: Path) -> str:
    from datetime import datetime, timezone

    st = fp.stat()
    return datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def _collect_open_tasks(workspace: Path, run_ids: list[str]) -> list[dict[str, Any]]:
    from contextful_sidecar.runtime.runs import WORKSPACE_INDEX_MODULE

    out: list[dict[str, Any]] = []
    for run_id in run_ids:
        run_dir = workspace / "runs" / run_id
        if not run_dir.is_dir():
            continue
        try:
            mod_dirs = sorted((p for p in run_dir.iterdir() if p.is_dir()), key=lambda p: p.name)
        except OSError:
            continue
        for mod_dir in mod_dirs:
            if mod_dir.name == WORKSPACE_INDEX_MODULE:
                continue
            tasks_path = mod_dir / "tasks.json"
            if not tasks_path.is_file():
                continue
            try:
                data = json.loads(tasks_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            module_id = str(data.get("moduleId") or mod_dir.name)
            for task in data.get("tasks") or []:
                if not isinstance(task, dict):
                    continue
                out.append({
                    "runId": run_id,
                    "moduleId": module_id,
                    "taskId": task.get("id"),
                    "title": task.get("title"),
                    "priority": task.get("priority"),
                })
    return out


def _compute_index_delta(workspace: Path, anchor_updated_at: str | None) -> dict[str, list[dict[str, Any]]]:
    from contextful_sidecar.runtime.indexing import _iter_meta_files, load_index

    new_items: list[dict[str, Any]] = []
    changed_items: list[dict[str, Any]] = []
    if not anchor_updated_at:
        return {"newItems": new_items, "changedItems": changed_items}

    index = load_index(workspace)
    items = index.get("items") or []
    indexed_ids_with_ts: set[str] = set()

    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "")
        indexed_at = item.get("indexedAt")
        content_updated_at = item.get("contentUpdatedAt")
        if indexed_at:
            indexed_ids_with_ts.add(item_id)
        if indexed_at and indexed_at > anchor_updated_at:
            new_items.append({
                "id": item_id,
                "type": item.get("type"),
                "path": item.get("path"),
                "indexedAt": indexed_at,
            })
        elif (
            content_updated_at
            and content_updated_at > anchor_updated_at
            and indexed_at
            and indexed_at <= anchor_updated_at
        ):
            changed_items.append({
                "id": item_id,
                "type": item.get("type"),
                "path": item.get("path"),
                "contentUpdatedAt": content_updated_at,
                "contentHash": item.get("contentHash"),
            })

    meta_dir = workspace / "meta"
    if meta_dir.is_dir():
        seen_paths = {i.get("path") for i in new_items + changed_items}
        for fp in _iter_meta_files(meta_dir):
            rel = fp.relative_to(workspace).as_posix()
            if rel in seen_paths:
                continue
            rel_meta = fp.relative_to(meta_dir).as_posix()
            item_id = f"meta:{rel_meta}"
            index_item = next((i for i in items if i.get("id") == item_id), None)
            if index_item and index_item.get("indexedAt"):
                continue
            try:
                mtime_iso = _mtime_iso(fp)
            except OSError:
                continue
            if mtime_iso > anchor_updated_at:
                new_items.append({
                    "id": item_id,
                    "type": "meta",
                    "path": rel,
                    "indexedAt": None,
                    "source": "mtime_fallback",
                })
    return {"newItems": new_items, "changedItems": changed_items}


def _gather_run_history(workspace: Path, trace: ToolTrace | None = None) -> str:
    from contextful_sidecar.runtime.runs import list_runs

    trace = trace or get_trace()
    if trace:
        trace.set_phase("history")

    runs = list_runs(workspace)
    analysis_runs = [
        r for r in runs
        if r.get("status") == "complete"
        and _has_analysis_modules(list(r.get("completedModules") or []))
    ]
    mode = "warm" if analysis_runs else "initial"
    anchor = analysis_runs[0] if analysis_runs else {}
    anchor_run_id = anchor.get("runId")
    anchor_updated_at = anchor.get("updatedAt")

    run_summaries: list[dict[str, Any]] = []
    for run in runs:
        run_id = str(run.get("runId") or "")
        if not run_id:
            continue
        summary_rel = f"runs/{run_id}/run-summary.md"
        summary_path = workspace / summary_rel
        run_summaries.append({
            "runId": run_id,
            "status": run.get("status"),
            "updatedAt": run.get("updatedAt"),
            "completedModules": list(run.get("completedModules") or []),
            "failedModule": run.get("failedModule"),
            "summaryPath": summary_rel if summary_path.is_file() else None,
        })

    recent_run_ids = [str(r.get("runId")) for r in runs[:3] if r.get("runId")]
    open_tasks = _collect_open_tasks(workspace, recent_run_ids)
    delta = _compute_index_delta(workspace, str(anchor_updated_at) if anchor_updated_at else None)

    payload = {
        "mode": mode,
        "anchorRunId": anchor_run_id,
        "anchorUpdatedAt": anchor_updated_at,
        "runs": run_summaries,
        "openTasks": open_tasks,
        "delta": delta,
    }
    return json.dumps(payload, indent=2)


# --- dispatcher (sync; run off-loop by the agent) -------------------------
def execute_tool(
    workspace: Path,
    name: str,
    args: dict[str, Any],
    trace: ToolTrace | None = None,
) -> str:
    workspace = Path(workspace)
    token = set_active_trace(trace) if trace else None
    reset_subprocess_budget(max_calls=1)
    try:
        if name == "read_file":
            return _read_file(
                workspace,
                args["path"],
                args.get("start_line"),
                args.get("end_line"),
                trace=trace,
            )
        if name == "list_directory":
            return _list_directory(workspace, args.get("path", "."), trace=trace)
        if name == "write_file":
            return _write_file(workspace, args.get("path", ""), args.get("content", ""))
        if name == "append_eventlog":
            append_eventlog(workspace, args["scope"], args["status"], args.get("message", ""))
            return "logged"
        if name == "write_analysis":
            return _write_analysis(workspace, args["module_id"], args["content"])
        if name == "write_tasks":
            return _write_tasks(workspace, args["module_id"], args["tasks_json"])
        if name == "grep_repo":
            return _grep_repo(
                workspace,
                args["pattern"],
                args.get("repo"),
                args.get("glob"),
                args.get("path"),
                trace=trace,
            )
        if name == "run_script":
            return _run_script(workspace, args["script"], args.get("args"), trace=trace)
        if name == "web_fetch":
            return _web_fetch(workspace, args["url"], args["filename"], trace=trace)
        if name == "web_search":
            return _web_search(workspace, args["query"])
        if name == "gather_context":
            return _gather_context(workspace, args["path"], trace=trace)
        if name == "gather_run_history":
            return _gather_run_history(workspace, trace=trace)
        return f"ERROR: unknown tool: {name}"
    except ValueError as exc:  # path escape and similar
        return f"ERROR: {exc}"
    except KeyError as exc:
        return f"ERROR: missing argument {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"
    finally:
        if token is not None:
            reset_active_trace(token)


def execute_readonly_tool(workspace: Path, name: str, args: dict[str, Any]) -> str:
    if name not in ORCHESTRATOR_READONLY_TOOLS:
        return f"ERROR: tool '{name}' is not available to the orchestrator"
    return execute_tool(workspace, name, args)
