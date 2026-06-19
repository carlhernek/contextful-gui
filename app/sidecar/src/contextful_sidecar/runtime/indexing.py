"""Workspace index: scan repos/meta/artefacts, LLM-enrich, merge user annotations."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from contextful_sidecar.runtime.eventlog import append_eventlog
from contextful_sidecar.runtime.openrouter import OpenRouterClient
from contextful_sidecar.runtime.step_log import log_step
from contextful_sidecar.runtime.tools import _git_env, _list_directory, _read_file, _silent_run

INDEX_FILE = ".workspace-index.json"
CACHE_FILE = ".index-cache.json"
ANNOTATIONS_FILE = ".index-annotations.json"
META_FILE = ".contextful.json"

ENRICH_CAP = 60
CONTENT_HEAD_CAP = 4096
REPO_TREE_DEPTH = 2
REPO_TREE_MAX_ENTRIES = 40
ARTIFACT_FILES = ("analysis.md", "tasks.json", "run-summary.md")
SKIP_DIR_NAMES = {".git", "node_modules", "target", "dist", "__pycache__", ".venv", "venv"}
BINARY_EXTENSIONS = {
    ".docx", ".doc", ".pdf", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".xlsx", ".xls", ".pptx", ".ppt", ".bin", ".exe", ".dll",
}
HASH_READ_CAP = 4 * 1024 * 1024       # 4MB max read for text hash
ARTIFACT_HASH_CAP = 512 * 1024        # 512KB for run artefacts
GIT_HEAD_TIMEOUT_SEC = 3.0
META_FILE_CAP = 500
SCAN_DEBUG_FILE = "scan-debug.json"

EventCallback = Callable[[str, Any], None]
CancelCheck = Callable[[], bool]

_ENRICH_SYSTEM = (
    "You index workspace items for an orchestrator agent. "
    "Return ONLY valid JSON: {\"description\": \"...\", \"keywords\": [\"...\"]}. "
    "description: one concise sentence (max 120 chars). "
    "keywords: 3-8 lowercase tokens relevant to the item."
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _sha1(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8", errors="replace")
    return hashlib.sha1(data).hexdigest()


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def _git_head(repo_dir: Path) -> str | None:
    if not repo_dir.joinpath(".git").exists():
        return None
    try:
        proc = _silent_run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            env=_git_env(),
            timeout=GIT_HEAD_TIMEOUT_SEC,
        )
        if proc.returncode == 0:
            return proc.stdout.strip() or None
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        pass
    return None


def _read_project_meta(workspace: Path) -> dict[str, Any]:
    meta = _read_json(workspace / META_FILE, {})
    return meta if isinstance(meta, dict) else {}


def _heuristic_description(item_type: str, name: str, snippet: str) -> str:
    if snippet.strip():
        for line in snippet.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                return re.sub(r"^#+\s*", "", line)[:120]
            return line[:120]
    if item_type == "repo":
        return f"Cloned repository {name}"
    if item_type == "meta":
        return f"Meta document {name}"
    return f"Run artefact {name}"


def _heuristic_keywords(item_type: str, name: str, path: str) -> list[str]:
    base = Path(name).stem.lower()
    parts = re.split(r"[-_./\\]+", f"{item_type} {path} {base}")
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        p = p.strip().lower()
        if len(p) >= 2 and p not in seen:
            seen.add(p)
            out.append(p)
        if len(out) >= 8:
            break
    return out


def _repo_tree_entries(workspace: Path, repo_name: str) -> list[dict[str, str]]:
    root = workspace / "repos" / repo_name
    if not root.is_dir():
        return []
    entries: list[dict[str, str]] = []

    def walk(base: Path, depth: int) -> None:
        if depth > REPO_TREE_DEPTH or len(entries) >= REPO_TREE_MAX_ENTRIES:
            return
        try:
            children = sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return
        for child in children:
            if child.name in SKIP_DIR_NAMES:
                continue
            rel = child.relative_to(workspace).as_posix()
            entries.append({"path": rel, "kind": "dir" if child.is_dir() else "file"})
            if len(entries) >= REPO_TREE_MAX_ENTRIES:
                return
            if child.is_dir():
                walk(child, depth + 1)

    walk(root, 0)
    return entries


def _content_snippet(workspace: Path, path: str, item_type: str, repo_name: str | None) -> str:
    if item_type == "repo" and repo_name:
        entries = _repo_tree_entries(workspace, repo_name)
        listing = _list_directory(workspace, f"repos/{repo_name}")
        return f"Tree sample:\n{json.dumps(entries[:20])}\n\nTop-level listing:\n{listing[:CONTENT_HEAD_CAP]}"
    full = workspace / path
    if full.is_file():
        try:
            data = full.read_bytes()[:CONTENT_HEAD_CAP]
            return data.decode("utf-8", errors="replace")
        except OSError:
            return ""
    return ""


def _stat_hash(path: Path) -> str:
    st = path.stat()
    mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
    return _sha1(f"stat:{st.st_size}:{mtime_ns}")


def _is_binary_path(path: Path) -> bool:
    return path.suffix.lower() in BINARY_EXTENSIONS


def _file_content_hash(path: Path, *, read_cap: int = HASH_READ_CAP) -> str:
    """Hash file content without reading large binaries whole."""
    if _is_binary_path(path):
        return _stat_hash(path)
    try:
        st = path.stat()
    except OSError:
        return _sha1("")
    if st.st_size > read_cap:
        return _stat_hash(path)
    try:
        raw = path.read_bytes()
    except OSError:
        return _sha1("")
    return _sha1(raw)


def _file_snippet(path: Path) -> str:
    if _is_binary_path(path):
        try:
            size = path.stat().st_size
        except OSError:
            return ""
        return f"binary file ({size} bytes)"
    try:
        data = path.read_bytes()[:CONTENT_HEAD_CAP]
        return data.decode("utf-8", errors="replace")
    except OSError:
        return ""


def _artifact_file_fields(fp: Path) -> tuple[str, str] | None:
    """Return (content_hash, snippet) for a run artefact, or None on skip."""
    try:
        st = fp.stat()
    except OSError:
        return None
    if st.st_size > ARTIFACT_HASH_CAP:
        return _stat_hash(fp), f"artefact ({st.st_size} bytes, hash from metadata)"
    try:
        raw = fp.read_bytes()
    except OSError:
        return None
    snippet = raw[:CONTENT_HEAD_CAP].decode("utf-8", errors="replace")
    return _sha1(raw), snippet


def _meta_file_fields(fp: Path) -> tuple[str, str, int] | None:
    try:
        st = fp.stat()
    except OSError:
        return None
    content_hash = _file_content_hash(fp)
    snippet = _file_snippet(fp)
    return content_hash, snippet, st.st_size


def _iter_meta_files(meta_dir: Path) -> list[Path]:
    """Stack walk of meta/ — no rglob, skip heavy dirs, cap file count."""
    out: list[Path] = []
    stack = [meta_dir]
    while stack and len(out) < META_FILE_CAP:
        current = stack.pop()
        try:
            children = sorted(current.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for child in children:
            if child.name in SKIP_DIR_NAMES or child.name.startswith("."):
                continue
            if child.is_dir():
                stack.append(child)
            elif child.is_file():
                out.append(child)
                if len(out) >= META_FILE_CAP:
                    break
    return sorted(out, key=lambda p: p.as_posix().lower())


def _scan_repo_item(workspace: Path, repo: dict[str, Any]) -> dict[str, Any]:
    """One index entry per configured repo — minimal work at scan time."""
    name = str(repo.get("name") or "").strip()
    repo_dir = workspace / "repos" / name
    cloned = repo_dir.is_dir() and repo_dir.joinpath(".git").exists()
    head = _git_head(repo_dir) if cloned else None
    rel_path = f"repos/{name}"
    if cloned:
        try:
            st = repo_dir.stat()
            mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
            content_hash = _sha1(f"{head or ''}:cloned:{mtime_ns}")
        except OSError:
            content_hash = _sha1(f"{head or ''}:cloned")
        snippet = f"Repository {name}" + (f" @ {head}" if head else "")
    else:
        content_hash = _sha1(f"uncloned:{name}")
        snippet = f"Repository {name} (not cloned)"
    return {
        "id": f"repo:{name}",
        "type": "repo",
        "path": rel_path,
        "name": name,
        "meta": {
            "url": repo.get("url", ""),
            "branch": repo.get("branch", "main"),
            "head": head,
            "cloned": cloned,
        },
        "entries": [],
        "contentHash": content_hash,
        "snippet": snippet,
    }


def _scan_meta_item(workspace: Path, meta_dir: Path, fp: Path) -> dict[str, Any]:
    rel = fp.relative_to(workspace).as_posix()
    rel_meta = fp.relative_to(meta_dir).as_posix()
    fields = _meta_file_fields(fp)
    if fields is None:
        raise OSError(f"cannot read meta file: {rel_meta}")
    content_hash, snippet, size = fields
    return {
        "id": f"meta:{rel_meta}",
        "type": "meta",
        "path": rel,
        "name": fp.name,
        "meta": {"size": size},
        "entries": [],
        "contentHash": content_hash,
        "snippet": snippet,
    }


class ScanTrace:
    """Collect per-step timing for scan debug dumps."""

    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self.started_at = _now_iso()

    def record(self, phase: str, **fields: Any) -> None:
        self.steps.append({"ts": _now_iso(), "phase": phase, **fields})


def _scan_debug_path(workspace: Path, run_id: str) -> Path:
    return workspace / "runs" / run_id / "workspace-index" / SCAN_DEBUG_FILE


def write_scan_debug(
    workspace: Path,
    run_id: str,
    trace: ScanTrace,
    *,
    error: str,
    items: list[dict[str, Any]] | None = None,
) -> Path:
    ws = Path(workspace)
    meta = _read_project_meta(ws)
    dump: dict[str, Any] = {
        "error": error,
        "startedAt": trace.started_at,
        "failedAt": _now_iso(),
        "runId": run_id,
        "projectRepos": meta.get("repos") or [],
        "itemCount": len(items or []),
        "itemsFound": [i.get("id") for i in (items or [])],
        "steps": trace.steps,
        "lastStep": trace.steps[-1] if trace.steps else None,
    }
    path = _scan_debug_path(ws, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dump, indent=2), encoding="utf-8")
    return path


def _scan_failure(
    ws: Path,
    run_id: str,
    trace: ScanTrace,
    error: str,
    items: list[dict[str, Any]] | None = None,
) -> None:
    debug_path = write_scan_debug(ws, run_id, trace, error=error, items=items)
    rel = debug_path.relative_to(ws).as_posix()
    summary_lines = [f"{s.get('phase')}: {s.get('itemId') or s.get('path', '')}" for s in trace.steps[-20:]]
    summary = "; ".join(summary_lines) if summary_lines else "no steps recorded"
    msg = f"{error} — debug={rel} — steps: {summary}"
    log_step(
        ws,
        scope="workspace-index",
        status="ERROR",
        message=msg,
        run_id=run_id,
        module_id="workspace-index",
        activity_kind="error",
        debugPath=rel,
    )


async def scan_items_async(
    workspace: Path,
    *,
    run_id: str | None = None,
    include_artifacts: bool = False,
    on_event: EventCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> tuple[list[dict[str, Any]], ScanTrace]:
    """Enumerate index items one-by-one with live logging (repos + meta files)."""
    from contextful_sidecar.runtime.index_agent import MODULE_ID

    ws = Path(workspace)
    on_event = on_event or (lambda _e, _d: None)
    should_cancel = should_cancel or (lambda: False)
    trace = ScanTrace()
    items: list[dict[str, Any]] = []
    meta_cfg = _read_project_meta(ws)

    def _log_item(status: str, message: str, item: dict[str, Any], *, ms: int) -> None:
        if not run_id:
            return
        log_step(
            ws,
            scope=MODULE_ID,
            status=status,
            message=message,
            run_id=run_id,
            module_id=MODULE_ID,
            activity_kind="scan_item",
            itemId=item["id"],
            path=item.get("path"),
            durationMs=ms,
        )
        on_event("index", {
            "phase": "scan_item",
            "itemId": item["id"],
            "path": item.get("path"),
            "module": MODULE_ID,
            "durationMs": ms,
        })

    trace.record("scan_begin", workspace=str(ws))
    repos = meta_cfg.get("repos") or []
    trace.record("repos_listed", count=len(repos))

    for repo in repos:
        if should_cancel():
            raise asyncio.CancelledError()
        if not isinstance(repo, dict):
            trace.record("repo_skip", reason="invalid entry")
            continue
        name = str(repo.get("name") or "").strip()
        if not name:
            trace.record("repo_skip", reason="empty name")
            continue
        t0 = time.monotonic()
        trace.record("repo_start", itemId=f"repo:{name}", path=f"repos/{name}")
        try:
            item = await asyncio.to_thread(_scan_repo_item, ws, repo)
        except Exception as exc:  # noqa: BLE001
            ms = int((time.monotonic() - t0) * 1000)
            trace.record("repo_error", itemId=f"repo:{name}", error=str(exc), durationMs=ms)
            raise RuntimeError(f"repo scan failed for {name}: {exc}") from exc
        ms = int((time.monotonic() - t0) * 1000)
        items.append(item)
        trace.record("repo_done", itemId=item["id"], durationMs=ms, cloned=item["meta"].get("cloned"))
        _log_item("SCAN_ITEM", f"repo {name} ({ms}ms)", item, ms=ms)
        await asyncio.sleep(0)

    meta_dir = ws / "meta"
    if meta_dir.is_dir():
        meta_files = await asyncio.to_thread(_iter_meta_files, meta_dir)
        trace.record("meta_listed", count=len(meta_files))
        if len(meta_files) >= META_FILE_CAP:
            append_eventlog(ws, MODULE_ID, "WARN", f"meta file cap {META_FILE_CAP} reached")
        for fp in meta_files:
            if should_cancel():
                raise asyncio.CancelledError()
            rel_meta = fp.relative_to(meta_dir).as_posix()
            t0 = time.monotonic()
            trace.record("meta_start", itemId=f"meta:{rel_meta}", path=fp.as_posix())
            try:
                item = await asyncio.to_thread(_scan_meta_item, ws, meta_dir, fp)
            except Exception as exc:  # noqa: BLE001
                ms = int((time.monotonic() - t0) * 1000)
                trace.record("meta_error", path=rel_meta, error=str(exc), durationMs=ms)
                raise RuntimeError(f"meta scan failed for {rel_meta}: {exc}") from exc
            ms = int((time.monotonic() - t0) * 1000)
            items.append(item)
            trace.record("meta_done", itemId=item["id"], durationMs=ms, size=item["meta"].get("size"))
            _log_item("SCAN_ITEM", f"meta {rel_meta} ({ms}ms)", item, ms=ms)
            await asyncio.sleep(0)
    else:
        trace.record("meta_missing", path=str(meta_dir))

    if include_artifacts:
        artifact_items = await asyncio.to_thread(_scan_artifact_items, ws)
        for item in artifact_items:
            items.append(item)
            trace.record("artifact_done", itemId=item["id"])

    trace.record("scan_complete", itemCount=len(items))
    return items, trace


def _scan_artifact_items(workspace: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    runs_dir = workspace / "runs"
    if not runs_dir.is_dir():
        return items
    run_dirs = sorted((p for p in runs_dir.iterdir() if p.is_dir()), reverse=True)[:5]
    for run_dir in run_dirs:
        run_id = run_dir.name
        for mod_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
            module_id = mod_dir.name
            for fname in ARTIFACT_FILES:
                fp = mod_dir / fname
                if not fp.is_file():
                    continue
                fields = _artifact_file_fields(fp)
                if fields is None:
                    continue
                content_hash, snippet = fields
                rel = fp.relative_to(workspace).as_posix()
                items.append({
                    "id": f"artifact:{run_id}/{module_id}/{fname}",
                    "type": "artifact",
                    "path": rel,
                    "name": fname,
                    "meta": {"runId": run_id, "moduleId": module_id, "size": fp.stat().st_size},
                    "entries": [],
                    "contentHash": content_hash,
                    "snippet": snippet,
                })
        summary = run_dir / "run-summary.md"
        if summary.is_file():
            fields = _artifact_file_fields(summary)
            if fields is not None:
                content_hash, snippet = fields
                rel = summary.relative_to(workspace).as_posix()
                items.append({
                    "id": f"artifact:{run_id}/run-summary.md",
                    "type": "artifact",
                    "path": rel,
                    "name": "run-summary.md",
                    "meta": {"runId": run_id, "moduleId": None, "size": summary.stat().st_size},
                    "entries": [],
                    "contentHash": content_hash,
                    "snippet": snippet,
                })
    return items


def scan_items(workspace: Path, *, include_artifacts: bool = True) -> list[dict[str, Any]]:
    """Synchronous full scan (tests + legacy refresh). Fast path: repos + meta only."""
    workspace = Path(workspace)
    meta = _read_project_meta(workspace)
    items: list[dict[str, Any]] = []

    for repo in meta.get("repos") or []:
        if isinstance(repo, dict) and str(repo.get("name") or "").strip():
            items.append(_scan_repo_item(workspace, repo))

    meta_dir = workspace / "meta"
    if meta_dir.is_dir():
        for fp in _iter_meta_files(meta_dir):
            try:
                items.append(_scan_meta_item(workspace, meta_dir, fp))
            except OSError:
                continue

    if include_artifacts:
        items.extend(_scan_artifact_items(workspace))

    return items


def load_annotations(workspace: Path) -> dict[str, Any]:
    data = _read_json(Path(workspace) / ANNOTATIONS_FILE, {})
    items = dict(data.get("items", {})) if isinstance(data, dict) else {}
    # Manual edits written straight into `.workspace-index.json` must survive re-index.
    for item in load_index(workspace).get("items") or []:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if not item_id:
            continue
        if item.get("userEdited") or item.get("source") == "user":
            items[str(item_id)] = {
                "description": item.get("description", ""),
                "keywords": item.get("keywords") or [],
            }
    return items


def load_cache(workspace: Path) -> dict[str, Any]:
    return _read_json(Path(workspace) / CACHE_FILE, {})


def load_index(workspace: Path) -> dict[str, Any]:
    data = _read_json(Path(workspace) / INDEX_FILE, {})
    if not isinstance(data, dict):
        return {"version": 1, "updatedAt": None, "project": {}, "items": []}
    data.setdefault("items", [])
    return data


def _parse_enrichment(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    desc = str(data.get("description") or "").strip()[:200]
    kws = data.get("keywords") or []
    if not isinstance(kws, list):
        kws = []
    keywords = [str(k).strip().lower() for k in kws if str(k).strip()][:12]
    return {"description": desc, "keywords": keywords}


async def _enrich_with_llm(
    *,
    client: OpenRouterClient,
    model: str,
    item: dict[str, Any],
) -> dict[str, Any]:
    user = (
        f"Item type: {item['type']}\n"
        f"Path: {item['path']}\n"
        f"Name: {item.get('name', '')}\n\n"
        f"Content preview:\n{(item.get('snippet') or '')[:CONTENT_HEAD_CAP]}"
    )
    response = await client.chat_completion(
        model=model,
        messages=[
            {"role": "system", "content": _ENRICH_SYSTEM},
            {"role": "user", "content": user},
        ],
    )
    content = response["choices"][0]["message"].get("content", "") or ""
    parsed = _parse_enrichment(content)
    if not parsed.get("description"):
        parsed["description"] = _heuristic_description(
            item["type"], item.get("name", ""), item.get("snippet") or ""
        )
    if not parsed.get("keywords"):
        parsed["keywords"] = _heuristic_keywords(item["type"], item.get("name", ""), item["path"])
    return parsed


def _merge_item(
    raw: dict[str, Any],
    *,
    annotations: dict[str, Any],
    cache: dict[str, Any],
    ai: dict[str, Any] | None,
    prefer_ai: bool = False,
) -> dict[str, Any]:
    item_id = raw["id"]
    user = annotations.get(item_id) if isinstance(annotations.get(item_id), dict) else {}
    cached = cache.get(item_id) if isinstance(cache.get(item_id), dict) else {}

    description = ""
    keywords: list[str] = []
    source = "heuristic"

    if prefer_ai and ai and (ai.get("description") or ai.get("keywords")):
        description = str(ai.get("description") or "")
        keywords = list(ai.get("keywords") or [])
        source = "ai"
    elif user.get("description") or user.get("keywords"):
        if user.get("description"):
            description = str(user["description"])
        elif cached.get("description"):
            description = str(cached["description"])
        elif ai and ai.get("description"):
            description = str(ai["description"])
        else:
            description = _heuristic_description(raw["type"], raw.get("name", ""), raw.get("snippet") or "")
        kw = user.get("keywords")
        if isinstance(kw, list) and kw:
            keywords = [str(k) for k in kw]
        elif cached.get("keywords"):
            keywords = [str(k) for k in cached["keywords"]]
        elif ai and ai.get("keywords"):
            keywords = list(ai["keywords"])
        else:
            keywords = _heuristic_keywords(raw["type"], raw.get("name", ""), raw["path"])
        source = "user"
    elif ai and (ai.get("description") or ai.get("keywords")):
        description = str(ai.get("description") or "")
        keywords = list(ai.get("keywords") or [])
        source = "ai"
    elif cached.get("description") or cached.get("keywords"):
        description = str(cached.get("description") or "")
        keywords = list(cached.get("keywords") or [])
        source = str(cached.get("source") or "ai")
    else:
        description = _heuristic_description(raw["type"], raw.get("name", ""), raw.get("snippet") or "")
        keywords = _heuristic_keywords(raw["type"], raw.get("name", ""), raw["path"])
        source = "heuristic"

    out: dict[str, Any] = {
        "id": item_id,
        "type": raw["type"],
        "path": raw["path"],
        "meta": raw.get("meta") or {},
        "entries": raw.get("entries") or [],
        "description": description,
        "keywords": keywords,
        "source": source,
        "contentHash": raw.get("contentHash"),
        "enrichedAt": _now_iso() if source in ("ai", "user") else None,
        "status": raw.get("status") or ("done" if description else "pending"),
    }
    if user:
        out["userEdited"] = True
    return out


def build_index_document(
    workspace: Path,
    merged_items: list[dict[str, Any]],
) -> dict[str, Any]:
    meta = _read_project_meta(workspace)
    return {
        "version": 1,
        "updatedAt": _now_iso(),
        "project": {
            "displayName": meta.get("display_name", ""),
            "projectType": meta.get("project_type", "both"),
        },
        "items": merged_items,
    }


async def refresh_index(
    *,
    workspace: str | Path,
    client: OpenRouterClient | None = None,
    models: dict[str, str] | None = None,
    on_event: EventCallback | None = None,
    should_cancel: CancelCheck | None = None,
    skip_enrichment: bool = False,
    force_item_ids: list[str] | None = None,
    force_enrich: bool = False,
) -> dict[str, Any]:
    ws = Path(workspace)
    on_event = on_event or (lambda _e, _d: None)
    should_cancel = should_cancel or (lambda: False)
    models = models or {}
    model = models.get("module") or models.get("orchestrator") or "deepseek/deepseek-v4-flash"

    raw_items = scan_items(ws)
    annotations = load_annotations(ws)
    cache = load_cache(ws)
    force_set = set(force_item_ids or [])
    enriched = 0
    merged: list[dict[str, Any]] = []

    for raw in raw_items:
        if should_cancel():
            break
        item_id = raw["id"]
        content_hash = raw.get("contentHash", "")
        user = annotations.get(item_id) if isinstance(annotations.get(item_id), dict) else {}
        has_user = bool(user.get("description") or user.get("keywords"))
        cached = cache.get(item_id) if isinstance(cache.get(item_id), dict) else {}
        cache_hit = cached.get("contentHash") == content_hash and cached.get("description")

        ai: dict[str, Any] | None = None
        need_enrich = (
            not skip_enrichment
            and client is not None
            and (force_enrich or not has_user)
            and (item_id in force_set or not cache_hit)
            and enriched < ENRICH_CAP
        )
        if need_enrich:
            on_event("index", {"itemId": item_id, "status": "enriching"})
            ai = await _enrich_with_llm(client=client, model=model, item=raw)
            cache[item_id] = {
                "contentHash": content_hash,
                "description": ai.get("description"),
                "keywords": ai.get("keywords"),
                "source": "ai",
                "enrichedAt": _now_iso(),
            }
            enriched += 1
            on_event("index", {"itemId": item_id, "status": "done"})
        elif cache_hit:
            ai = {
                "description": cached.get("description"),
                "keywords": cached.get("keywords"),
            }

        merged.append(_merge_item(
            raw,
            annotations=annotations,
            cache=cache,
            ai=ai,
            prefer_ai=force_enrich and item_id in force_set,
        ))

    doc = build_index_document(ws, merged)
    _write_json_atomic(ws / INDEX_FILE, doc)
    _write_json_atomic(ws / CACHE_FILE, cache)
    return {"ok": True, "itemCount": len(merged), "enriched": enriched, "updatedAt": doc["updatedAt"]}


def _item_already_indexed(
    raw: dict[str, Any],
    *,
    annotations: dict[str, Any],
    cache: dict[str, Any],
) -> bool:
    item_id = raw["id"]
    content_hash = raw.get("contentHash", "")
    user = annotations.get(item_id) if isinstance(annotations.get(item_id), dict) else {}
    if user.get("description") or user.get("keywords"):
        return True
    cached = cache.get(item_id) if isinstance(cache.get(item_id), dict) else {}
    return cached.get("contentHash") == content_hash and bool(cached.get("description"))


async def agentic_reindex(
    *,
    workspace: str | Path,
    run_id: str,
    client: OpenRouterClient | None = None,
    models: dict[str, str] | None = None,
    on_event: EventCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> dict[str, Any]:
    """Two-phase agentic indexer: enumerate all items, then one bounded agent per item."""
    from contextful_sidecar.runtime.activity import append_activity
    from contextful_sidecar.runtime.index_agent import MODULE_ID, index_item

    ws = Path(workspace)
    on_event = on_event or (lambda _e, _d: None)
    should_cancel = should_cancel or (lambda: False)
    models = models or {}
    model = models.get("module") or models.get("orchestrator") or "deepseek/deepseek-v4-flash"

    log_step(
        ws,
        scope=MODULE_ID,
        status="SCAN_START",
        message="enumerating repos + meta files (1 item each)",
        run_id=run_id,
        module_id=MODULE_ID,
        activity_kind="scan_start",
    )
    scan_t0 = time.monotonic()
    try:
        raw_items, trace = await scan_items_async(
            ws,
            run_id=run_id,
            include_artifacts=False,
            on_event=on_event,
            should_cancel=should_cancel,
        )
    except asyncio.CancelledError:
        _scan_failure(ws, run_id, trace, "scan cancelled", items=[])
        raise
    except Exception as exc:
        _scan_failure(ws, run_id, trace, str(exc), items=[])
        raise
    scan_ms = int((time.monotonic() - scan_t0) * 1000)
    total = len(raw_items)
    log_step(
        ws,
        scope=MODULE_ID,
        status="SCAN_DONE",
        message=f"{total} items in {scan_ms}ms",
        run_id=run_id,
        module_id=MODULE_ID,
        activity_kind="scan_done",
        itemCount=total,
        durationMs=scan_ms,
    )

    annotations = load_annotations(ws)
    cache = load_cache(ws)

    append_eventlog(ws, MODULE_ID, "ENUMERATE", f"{total} items")
    on_event("index", {"phase": "enumerate", "total": total, "module": MODULE_ID})

    skeleton: list[dict[str, Any]] = []
    for raw in raw_items:
        if _item_already_indexed(raw, annotations=annotations, cache=cache):
            merged = _merge_item(raw, annotations=annotations, cache=cache, ai=None)
            merged["status"] = "cached"
        else:
            merged = _merge_item(raw, annotations=annotations, cache=cache, ai=None)
            merged["status"] = "pending"
            merged["description"] = merged.get("description") or ""
        skeleton.append(merged)

    doc = build_index_document(ws, skeleton)
    _write_json_atomic(ws / INDEX_FILE, doc)

    enriched = 0
    skipped = 0
    pending_items = [r for r in raw_items if not _item_already_indexed(r, annotations=annotations, cache=cache)]
    pending_total = len(pending_items)

    for idx, raw in enumerate(raw_items, start=1):
        if should_cancel():
            break
        item_id = raw["id"]
        if _item_already_indexed(raw, annotations=annotations, cache=cache):
            skipped += 1
            log_step(
                ws,
                scope=MODULE_ID,
                status="CACHE_HIT",
                message=f"{item_id} ({idx}/{total})",
                run_id=run_id,
                module_id=MODULE_ID,
                activity_kind="cache_hit",
                itemId=item_id,
                itemIndex=idx,
                itemTotal=total,
            )
            on_event(
                "index",
                {
                    "itemId": item_id,
                    "status": "skipped",
                    "index": idx,
                    "total": total,
                    "module": MODULE_ID,
                },
            )
            append_activity(
                ws,
                run_id,
                MODULE_ID,
                "item",
                status="skipped",
                itemId=item_id,
                itemIndex=idx,
                itemTotal=total,
                path=raw.get("path"),
            )
            continue

        log_step(
            ws,
            scope=MODULE_ID,
            status="CACHE_MISS",
            message=f"{item_id} ({idx}/{total})",
            run_id=run_id,
            module_id=MODULE_ID,
            activity_kind="cache_miss",
            itemId=item_id,
            itemIndex=idx,
            itemTotal=total,
        )
        log_step(
            ws,
            scope=MODULE_ID,
            status="INDEX_START",
            message=f"{item_id} ({idx}/{total}) path={raw.get('path', '')}",
            run_id=run_id,
            module_id=MODULE_ID,
            activity_kind="index_start",
            itemId=item_id,
            itemIndex=idx,
            itemTotal=total,
            path=raw.get("path"),
        )
        on_event(
            "index",
            {
                "itemId": item_id,
                "status": "indexing",
                "index": idx,
                "total": total,
                "module": MODULE_ID,
            },
        )

        if client is None:
            ai = {
                "description": _heuristic_description(raw["type"], raw.get("name", ""), raw.get("snippet") or ""),
                "keywords": _heuristic_keywords(raw["type"], raw.get("name", ""), raw["path"]),
            }
        else:
            ai = await index_item(
                workspace=ws,
                run_id=run_id,
                item=raw,
                item_index=idx,
                item_total=total,
                model=model,
                client=client,
                on_event=on_event,
                should_cancel=should_cancel,
            )

        if should_cancel():
            break

        content_hash = raw.get("contentHash", "")
        cache[item_id] = {
            "contentHash": content_hash,
            "description": ai.get("description"),
            "keywords": ai.get("keywords"),
            "source": "ai",
            "enrichedAt": _now_iso(),
        }
        enriched += 1

        for i, entry in enumerate(skeleton):
            if entry["id"] == item_id:
                skeleton[i] = _merge_item(
                    raw,
                    annotations=annotations,
                    cache=cache,
                    ai=ai,
                    prefer_ai=True,
                )
                skeleton[i]["status"] = "done"
                break

        doc = build_index_document(ws, skeleton)
        _write_json_atomic(ws / INDEX_FILE, doc)
        _write_json_atomic(ws / CACHE_FILE, cache)

        on_event(
            "index",
            {
                "itemId": item_id,
                "status": "done",
                "index": idx,
                "total": total,
                "module": MODULE_ID,
            },
        )
        log_step(
            ws,
            scope=MODULE_ID,
            status="INDEX_DONE",
            message=f"{item_id} source=ai ({idx}/{total})",
            run_id=run_id,
            module_id=MODULE_ID,
            activity_kind="index_done",
            itemId=item_id,
            itemIndex=idx,
            itemTotal=total,
            description=ai.get("description"),
            source="ai",
        )
        append_activity(
            ws,
            run_id,
            MODULE_ID,
            "item",
            status="done",
            itemId=item_id,
            itemIndex=idx,
            itemTotal=total,
            path=raw.get("path"),
            description=ai.get("description"),
        )

    final_doc = build_index_document(ws, skeleton)
    _write_json_atomic(ws / INDEX_FILE, final_doc)
    _write_json_atomic(ws / CACHE_FILE, cache)

    return {
        "ok": True,
        "itemCount": len(skeleton),
        "enriched": enriched,
        "skipped": skipped,
        "pendingTotal": pending_total,
        "updatedAt": final_doc["updatedAt"],
    }


def format_index_for_prompt(index: dict[str, Any]) -> str:
    items = index.get("items") or []
    if not items:
        return "Workspace index: (empty — run refresh or add repos/meta documents)\n"

    lines = ["Workspace index:"]
    by_type: dict[str, list[dict[str, Any]]] = {"repo": [], "meta": [], "artifact": []}
    for item in items:
        t = item.get("type", "")
        if t in by_type:
            by_type[t].append(item)

    if by_type["repo"]:
        lines.append("\nRepositories:")
        for it in by_type["repo"]:
            meta = it.get("meta") or {}
            head = meta.get("head")
            cloned = meta.get("cloned")
            kw = ", ".join(it.get("keywords") or [])
            lines.append(
                f"  - {it['id']} path={it['path']} cloned={cloned}"
                + (f" head={head}" if head else "")
                + f"\n    {it.get('description', '')}"
                + (f"\n    keywords: {kw}" if kw else "")
            )

    if by_type["meta"]:
        lines.append("\nMeta documents:")
        for it in by_type["meta"][:40]:
            kw = ", ".join(it.get("keywords") or [])
            lines.append(
                f"  - {it['path']}: {it.get('description', '')}"
                + (f" [{kw}]" if kw else "")
            )

    if by_type["artifact"]:
        lines.append("\nRecent run artefacts:")
        for it in by_type["artifact"][:30]:
            lines.append(f"  - {it['path']}: {it.get('description', '')}")

    return "\n".join(lines) + "\n"
