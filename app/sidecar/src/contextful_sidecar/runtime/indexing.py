"""Workspace index: scan repos/meta/artefacts, LLM-enrich, merge user annotations."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from contextful_sidecar.runtime.openrouter import OpenRouterClient
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
            timeout=15,
        )
        if proc.returncode == 0:
            return proc.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
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


def scan_items(workspace: Path) -> list[dict[str, Any]]:
    workspace = Path(workspace)
    meta = _read_project_meta(workspace)
    items: list[dict[str, Any]] = []

    for repo in meta.get("repos") or []:
        if not isinstance(repo, dict):
            continue
        name = str(repo.get("name") or "").strip()
        if not name:
            continue
        repo_dir = workspace / "repos" / name
        cloned = repo_dir.joinpath(".git").exists()
        head = _git_head(repo_dir) if cloned else None
        rel_path = f"repos/{name}"
        snippet = _content_snippet(workspace, rel_path, "repo", name) if cloned else ""
        content_hash = _sha1(f"{head or ''}:{snippet[:512]}")
        items.append({
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
            "entries": _repo_tree_entries(workspace, name) if cloned else [],
            "contentHash": content_hash,
            "snippet": snippet,
        })

    meta_dir = workspace / "meta"
    if meta_dir.is_dir():
        for fp in sorted(meta_dir.rglob("*")):
            if not fp.is_file():
                continue
            rel = fp.relative_to(workspace).as_posix()
            rel_meta = fp.relative_to(meta_dir).as_posix()
            try:
                raw = fp.read_bytes()
            except OSError:
                continue
            snippet = raw[:CONTENT_HEAD_CAP].decode("utf-8", errors="replace")
            items.append({
                "id": f"meta:{rel_meta}",
                "type": "meta",
                "path": rel,
                "name": fp.name,
                "meta": {"size": fp.stat().st_size},
                "entries": [],
                "contentHash": _sha1(raw),
                "snippet": snippet,
            })

    runs_dir = workspace / "runs"
    if runs_dir.is_dir():
        run_dirs = sorted((p for p in runs_dir.iterdir() if p.is_dir()), reverse=True)[:5]
        for run_dir in run_dirs:
            run_id = run_dir.name
            for mod_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
                module_id = mod_dir.name
                for fname in ARTIFACT_FILES:
                    fp = mod_dir / fname
                    if not fp.is_file():
                        continue
                    try:
                        raw = fp.read_bytes()
                    except OSError:
                        continue
                    rel = fp.relative_to(workspace).as_posix()
                    snippet = raw[:CONTENT_HEAD_CAP].decode("utf-8", errors="replace")
                    items.append({
                        "id": f"artifact:{run_id}/{module_id}/{fname}",
                        "type": "artifact",
                        "path": rel,
                        "name": fname,
                        "meta": {"runId": run_id, "moduleId": module_id, "size": fp.stat().st_size},
                        "entries": [],
                        "contentHash": _sha1(raw),
                        "snippet": snippet,
                    })
            summary = run_dir / "run-summary.md"
            if summary.is_file():
                try:
                    raw = summary.read_bytes()
                except OSError:
                    continue
                rel = summary.relative_to(workspace).as_posix()
                snippet = raw[:CONTENT_HEAD_CAP].decode("utf-8", errors="replace")
                items.append({
                    "id": f"artifact:{run_id}/run-summary.md",
                    "type": "artifact",
                    "path": rel,
                    "name": "run-summary.md",
                    "meta": {"runId": run_id, "moduleId": None, "size": summary.stat().st_size},
                    "entries": [],
                    "contentHash": _sha1(raw),
                    "snippet": snippet,
                })

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
