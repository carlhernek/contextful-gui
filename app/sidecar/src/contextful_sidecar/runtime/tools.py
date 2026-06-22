"""Tools layer: sandboxed file IO, code search, scripts, and web research (spec section 6)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import certifi
import httpx

from contextful_sidecar.runtime.file_text import is_binary_path, read_file_as_text, read_text_snippet
from contextful_sidecar.runtime.eventlog import append_eventlog
from contextful_sidecar.runtime.repo_path_policy import check_repo_path, filter_repo_children

READ_FILE_CAP = 500_000          # 500KB cap for read_file
WEB_FETCH_CAP = 100_000          # 100k chars for web_fetch
RUN_SCRIPT_TIMEOUT = 120         # seconds
RUN_SCRIPT_OUTPUT_CAP = 8000     # chars
GREP_MAX_MATCHES = 200
GREP_MAX_LINE_LEN = 400
GATHER_CONTEXT_CAP = 24_000        # chars for gather_context bundle
GATHER_DOC_EXCERPT = 3000          # per doc file excerpt
GATHER_TREE_DEPTH = 2
GATHER_TREE_MAX = 40

# Extra ripgrep globs — block common secrets even if tracked in git.
_RG_SECRET_GLOBS = (
    "!.env", "!.env.*", "!.npmrc", "!.pypirc", "!.netrc", "!.htpasswd",
    "!*.pem", "!*.key", "!*.p12", "!*.pfx", "!*.jks", "!*.keystore",
    "!id_rsa", "!id_dsa", "!id_ecdsa", "!id_ed25519",
    "!**/.ssh/**",
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
            "description": "Read a UTF-8 text file within the workspace (500KB cap).",
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
            "description": "Bounded ripgrep over read-only target repos.",
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
                "Gather a condensed context bundle for an item path: README/docs, API specs, "
                "stack manifests, and a bounded directory tree. Prefer this for repos and large trees."
            ),
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
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
def _read_file(workspace: Path, path: str) -> str:
    target = _resolve(workspace, path)
    if target.is_dir():
        return f"ERROR: '{path}' is a directory; use list_directory"
    if not target.exists():
        return "ERROR: file not found"
    if blocked := check_repo_path(workspace, target):
        return blocked
    return read_file_as_text(target, cap=READ_FILE_CAP)


def _list_directory(workspace: Path, path: str = ".") -> str:
    target = _resolve(workspace, path)
    if not target.exists():
        return "ERROR: directory not found"
    if not target.is_dir():
        return f"ERROR: '{path}' is not a directory"
    entries = []
    for child in filter_repo_children(workspace, target):
        kind = "dir" if child.is_dir() else "file"
        entries.append(f"{kind}\t{child.name}")
    hint = "(note: a '.git' entry inside a worktree is a pointer file, not a directory)"
    return "\n".join(entries) + f"\n{hint}" if entries else f"(empty)\n{hint}"


def _write_file(workspace: Path, path: str, content: str) -> str:
    if not path or not path.strip():
        return "ERROR: empty path"
    target = _resolve(workspace, path)
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
    from contextful_sidecar.runtime.schema import validate_tasks
    error = validate_tasks(parsed)
    if error:
        return f"ERROR: tasks failed schema validation: {error}"
    rel = f"runs/{run_id}/{module_id}/tasks.json"
    return _write_file(workspace, rel, json.dumps(parsed, indent=2))


def _grep_repo(workspace: Path, pattern: str, repo: str | None = None,
               glob: str | None = None, path: str | None = None) -> str:
    if glob and any(glob.lower().endswith(ext) for ext in (
        ".docx", ".doc", ".pdf", ".zip", ".png", ".jpg", ".xlsx", ".pptx",
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
    rg = shutil.which("rg")
    if rg:
        args = [rg, "--line-number", "--no-heading", "--color", "never",
                "--max-count", str(GREP_MAX_MATCHES)]
        if glob:
            args += ["--glob", glob]
        for secret_glob in _RG_SECRET_GLOBS:
            args += ["--glob", secret_glob]
        args += [pattern, str(search_root)]
        try:
            proc = _silent_run(args, capture_output=True, text=True, timeout=60)
            out = proc.stdout or proc.stderr or "(no matches)"
        except (subprocess.TimeoutExpired, OSError) as exc:
            return f"ERROR: grep failed: {exc}"
    else:
        out = _python_grep(workspace, search_root, pattern, glob)
    return _cap_grep_output(_filter_grep_output(workspace, out))


def _filter_grep_output(workspace: Path, out: str) -> str:
    if not out or out in {"(no matches)", "(no output)"}:
        return out
    kept: list[str] = []
    for line in out.splitlines():
        colon = line.find(":")
        if colon <= 0:
            kept.append(line)
            continue
        file_path = Path(line[:colon])
        if check_repo_path(workspace, file_path) is None:
            kept.append(line)
    return "\n".join(kept) if kept else "(no matches)"


def _python_grep(workspace: Path, root: Path, pattern: str, glob: str | None) -> str:
    import re
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return f"ERROR: bad pattern: {exc}"
    matches: list[str] = []
    files = root.rglob(glob) if glob else root.rglob("*")
    for f in files:
        if not f.is_file():
            continue
        if check_repo_path(workspace, f) is not None:
            continue
        try:
            for i, line in enumerate(f.open("r", encoding="utf-8", errors="replace"), 1):
                if rx.search(line):
                    matches.append(f"{f}:{i}:{line.rstrip()}")
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


def _run_script(workspace: Path, script: str, args: list[str] | None = None) -> str:
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


def _web_fetch(workspace: Path, url: str, filename: str) -> str:
    if not (url.startswith("http://") or url.startswith("https://")):
        return "ERROR: web_fetch only supports http/https URLs"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; Contextful/1.0)"}
    try:
        with httpx.Client(timeout=30, follow_redirects=True, verify=certifi.where()) as c:
            r = c.get(url, headers=headers)
            r.raise_for_status()
            text = r.text
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: fetch failed: {exc}"
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


def _gather_tree(workspace: Path, root: Path) -> list[str]:
    lines: list[str] = []

    def walk(base: Path, depth: int) -> None:
        if depth > GATHER_TREE_DEPTH or len(lines) >= GATHER_TREE_MAX:
            return
        try:
            children = filter_repo_children(workspace, base)
        except OSError:
            return
        for child in children:
            if child.name in {".git", "node_modules", "target", "dist", "__pycache__", ".venv", "venv"}:
                continue
            rel = child.relative_to(workspace).as_posix()
            prefix = "dir" if child.is_dir() else "file"
            lines.append(f"{prefix}\t{rel}")
            if len(lines) >= GATHER_TREE_MAX:
                return
            if child.is_dir():
                walk(child, depth + 1)

    if root.is_dir():
        walk(root, 0)
    return lines


def _gather_context(workspace: Path, path: str) -> str:
    target = _resolve(workspace, path)
    if not target.exists():
        return f"ERROR: path not found: {path}"
    if blocked := check_repo_path(workspace, target):
        return blocked

    sections: list[str] = [f"# Context bundle for {path}\n"]

    doc_candidates: list[Path] = []
    if target.is_file():
        doc_candidates.append(target)
    else:
        for name in ("README.md", "README", "README.txt", "ARCHITECTURE.md", "CONTRIBUTING.md"):
            p = target / name
            if p.is_file():
                doc_candidates.append(p)
        docs_dir = target / "docs"
        if docs_dir.is_dir():
            for fp in sorted(docs_dir.rglob("*")):
                if not fp.is_file() or fp.suffix.lower() not in {".md", ".txt", ".rst"}:
                    continue
                if check_repo_path(workspace, fp) is not None:
                    continue
                doc_candidates.append(fp)
                if len(doc_candidates) >= 8:
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
    search_root = target if target.is_dir() else target.parent
    for name in manifest_names:
        fp = search_root / name
        if fp.is_file() and check_repo_path(workspace, fp) is None:
            manifests.append(f"### {fp.relative_to(workspace).as_posix()}\n{_excerpt_file(fp, 2000)}\n")
    if manifests:
        sections.append("## Stack manifests\n" + "\n".join(manifests))

    api_patterns = ("openapi", "swagger", "graphql")
    api_files: list[str] = []
    if search_root.is_dir():
        for fp in sorted(search_root.rglob("*")):
            if not fp.is_file() or check_repo_path(workspace, fp) is not None:
                continue
            lower = fp.name.lower()
            if any(p in lower for p in api_patterns) and fp.suffix.lower() in {".yaml", ".yml", ".json", ".graphql", ".gql"}:
                rel = fp.relative_to(workspace).as_posix()
                api_files.append(f"### {rel}\n{_excerpt_file(fp, 4000)}\n")
                if len(api_files) >= 4:
                    break
    if api_files:
        sections.append("## API specs\n" + "\n".join(api_files))

    tree_root = target if target.is_dir() else target.parent
    tree_lines = _gather_tree(workspace, tree_root)
    if tree_lines:
        sections.append("## Directory tree (bounded)\n" + "\n".join(tree_lines))

    out = "\n".join(sections)
    if len(out) > GATHER_CONTEXT_CAP:
        out = out[:GATHER_CONTEXT_CAP] + "\n...[truncated]"
    return out or f"(no context gathered for {path})"


# --- dispatcher (sync; run off-loop by the agent) -------------------------
def execute_tool(workspace: Path, name: str, args: dict[str, Any]) -> str:
    workspace = Path(workspace)
    try:
        if name == "read_file":
            return _read_file(workspace, args["path"])
        if name == "list_directory":
            return _list_directory(workspace, args.get("path", "."))
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
                workspace, args["pattern"], args.get("repo"), args.get("glob"), args.get("path"),
            )
        if name == "run_script":
            return _run_script(workspace, args["script"], args.get("args"))
        if name == "web_fetch":
            return _web_fetch(workspace, args["url"], args["filename"])
        if name == "web_search":
            return _web_search(workspace, args["query"])
        if name == "gather_context":
            return _gather_context(workspace, args["path"])
        return f"ERROR: unknown tool: {name}"
    except ValueError as exc:  # path escape and similar
        return f"ERROR: {exc}"
    except KeyError as exc:
        return f"ERROR: missing argument {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def execute_readonly_tool(workspace: Path, name: str, args: dict[str, Any]) -> str:
    if name not in ORCHESTRATOR_READONLY_TOOLS:
        return f"ERROR: tool '{name}' is not available to the orchestrator"
    return execute_tool(workspace, name, args)
