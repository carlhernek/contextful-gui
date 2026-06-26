"""Supabase Management API client (read-only, config + advisors only).

Hard constraint: this module NEVER issues SQL and NEVER reads database
contents. It only calls a fixed allowlist of GET endpoints on
``api.supabase.com/v1`` to capture project *configuration* and *advisor*
findings, then writes metadata-only JSON artifacts under ``supabase/<subdir>/``.

The personal access token (PAT) is passed per-call as an in-memory RPC param
and is never written to disk or to any artifact.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import certifi
import httpx

from contextful_sidecar.runtime.guard import run_guarded
from contextful_sidecar.runtime.step_log import log_step

MGMT_BASE = "https://api.supabase.com/v1"
_TIMEOUT = 30
# Wall-clock guard per Management API GET (retried on timeout/transient error).
_GUARD_TIMEOUT_SEC = 60.0
_SCOPE = "supabase"


def _headers(pat: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {pat.strip()}",
        "Accept": "application/json",
        "User-Agent": "Contextful",
    }


def _log(workspace: str | Path | None, status: str, message: str) -> None:
    """Verbose, best-effort step logging to the workspace .eventlog.

    Logging must never abort a Management API call, and must NEVER include the
    personal access token — only its presence/length is ever referenced.
    """
    if not workspace:
        return
    try:
        log_step(Path(workspace), scope=_SCOPE, status=status, message=message)
    except Exception:  # noqa: BLE001 — logging is best-effort only
        pass


def _token_fingerprint(pat: str) -> str:
    """Non-secret descriptor of the PAT for logs (length + prefix marker only)."""
    has_prefix = pat.startswith("sbp_")
    return f"tokenLen={len(pat)} sbpPrefix={has_prefix}"


async def list_projects(pat: str, workspace: str | Path | None = None) -> list[dict[str, Any]]:
    """GET /v1/projects -> [{ ref, name, region, status, created_at }]."""
    pat = (pat or "").strip()
    if not pat:
        _log(workspace, "ERROR", "list_projects aborted — missing personal access token")
        raise ValueError("missing Supabase personal access token")
    url = f"{MGMT_BASE}/projects"
    _log(
        workspace,
        "START",
        f"list_projects — GET {url} ({_token_fingerprint(pat)})",
    )
    t0 = time.monotonic()
    async with httpx.AsyncClient(verify=certifi.where(), timeout=_TIMEOUT) as c:
        try:
            r = await run_guarded(
                lambda: c.get(url, headers=_headers(pat)),
                label="supabase list_projects",
                scope=_SCOPE,
                workspace=Path(workspace) if workspace else None,
                timeout_sec=_GUARD_TIMEOUT_SEC,
            )
        except httpx.HTTPError as exc:
            ms = int((time.monotonic() - t0) * 1000)
            _log(workspace, "ERROR", f"list_projects request error after {ms}ms — {exc}")
            raise
        ms = int((time.monotonic() - t0) * 1000)
        _log(workspace, "HTTP", f"GET /projects -> HTTP {r.status_code} ({ms}ms)")
        if r.status_code in (401, 403):
            _log(
                workspace,
                "ERROR",
                f"list_projects rejected — HTTP {r.status_code} (invalid/expired token)",
            )
            raise RuntimeError(
                "Supabase rejected the token (401/403). Check the PAT is valid "
                "and has account access."
            )
        r.raise_for_status()
        data = r.json()
    projects: list[dict[str, Any]] = []
    for p in data if isinstance(data, list) else []:
        projects.append(
            {
                "ref": p.get("id") or p.get("ref"),
                "name": p.get("name"),
                "region": p.get("region"),
                "status": p.get("status"),
                "created_at": p.get("created_at"),
                "organization_id": p.get("organization_id"),
            }
        )
    refs = ", ".join(str(p["ref"]) for p in projects) or "none"
    _log(
        workspace,
        "SUCCESS",
        f"list_projects — {len(projects)} project(s) returned [{refs}]",
    )
    return projects


# Each entry: (artifact filename, endpoint path under /projects/{ref}).
# Every call is a GET. None of these return row data.
_SNAPSHOT_ENDPOINTS: list[tuple[str, str]] = [
    ("auth.json", "config/auth"),
    ("api.json", "postgrest"),
    ("storage.json", "config/storage"),
    ("functions.json", "functions"),
    ("database_postgres.json", "config/database/postgres"),
    ("database_ssl.json", "config/database/ssl-enforcement"),
    ("database_pooler.json", "config/database/pooler"),
    ("network.json", "network-restrictions"),
    ("api_keys.json", "api-keys"),
    ("advisors_security.json", "advisors/security"),
    ("advisors_performance.json", "advisors/performance"),
]

# Endpoints allowed to fail softly (deprecated / plan-gated / not-found).
_BEST_EFFORT = {
    "advisors/security",
    "advisors/performance",
    "network-restrictions",
    "config/database/pooler",
    "config/database/ssl-enforcement",
    "api-keys",
}


def _redact_api_keys(payload: Any) -> Any:
    """Keep key *types/names/presence* but never the secret values."""
    redacted: list[dict[str, Any]] = []
    items = payload if isinstance(payload, list) else payload.get("data", []) if isinstance(payload, dict) else []
    for k in items if isinstance(items, list) else []:
        if not isinstance(k, dict):
            continue
        redacted.append(
            {
                "name": k.get("name"),
                "type": k.get("type"),
                "id": k.get("id"),
                "present": bool(k.get("api_key") or k.get("secret") or k.get("hash")),
            }
        )
    return redacted


async def snapshot(
    *,
    pat: str,
    project_ref: str,
    name: str,
    region: str | None,
    status: str | None,
    workspace: str,
    subdir: str,
) -> dict[str, Any]:
    """Snapshot one project's configuration into ``supabase/<subdir>/*.json``.

    Returns a summary dict: { written: [...], skipped: [{path, reason}], region }.
    """
    pat = (pat or "").strip()
    if not pat:
        _log(workspace, "ERROR", f"snapshot aborted — missing token (ref={project_ref})")
        raise ValueError("missing Supabase personal access token")
    if not project_ref:
        _log(workspace, "ERROR", "snapshot aborted — missing project ref")
        raise ValueError("missing project ref")

    out_dir = Path(workspace) / "supabase" / subdir
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    skipped: list[dict[str, str]] = []
    headers = _headers(pat)
    total = len(_SNAPSHOT_ENDPOINTS)
    _log(
        workspace,
        "START",
        f"snapshot START — name={name!r} ref={project_ref} region={region} "
        f"status={status} subdir={subdir} endpoints={total} "
        f"out={out_dir} ({_token_fingerprint(pat)})",
    )
    t_all = time.monotonic()

    async with httpx.AsyncClient(verify=certifi.where(), timeout=_TIMEOUT) as c:
        for idx, (filename, path) in enumerate(_SNAPSHOT_ENDPOINTS, start=1):
            url = f"{MGMT_BASE}/projects/{project_ref}/{path}"
            _log(workspace, "REQUEST", f"[{idx}/{total}] GET projects/{project_ref}/{path}")
            t0 = time.monotonic()
            try:
                r = await run_guarded(
                    lambda u=url: c.get(u, headers=headers),
                    label=f"supabase GET {path}",
                    scope=_SCOPE,
                    workspace=Path(workspace) if workspace else None,
                    timeout_sec=_GUARD_TIMEOUT_SEC,
                )
            except (httpx.HTTPError, RuntimeError) as exc:
                ms = int((time.monotonic() - t0) * 1000)
                reason = f"request error: {exc}"
                skipped.append({"path": path, "reason": reason})
                _log(workspace, "WARN", f"[{idx}/{total}] {path} skipped after {ms}ms — {reason}")
                continue
            ms = int((time.monotonic() - t0) * 1000)
            if r.status_code == 200:
                try:
                    body = r.json()
                except ValueError:
                    skipped.append({"path": path, "reason": "non-JSON response"})
                    _log(
                        workspace,
                        "WARN",
                        f"[{idx}/{total}] {path} skipped — HTTP 200 but non-JSON body ({ms}ms)",
                    )
                    continue
                redacted = path == "api-keys"
                if redacted:
                    body = _redact_api_keys(body)
                nbytes = _write_artifact(out_dir / filename, body)
                written.append(filename)
                _log(
                    workspace,
                    "WRITE",
                    f"[{idx}/{total}] {path} -> HTTP 200 ({ms}ms) wrote {filename} "
                    f"({nbytes}B){' [api keys redacted to presence-only]' if redacted else ''}",
                )
            elif r.status_code in (401, 403):
                _log(
                    workspace,
                    "ERROR",
                    f"[{idx}/{total}] {path} -> HTTP {r.status_code} ({ms}ms) — token rejected, aborting",
                )
                raise RuntimeError(
                    f"Supabase rejected the token for {path} ({r.status_code})."
                )
            elif path in _BEST_EFFORT and r.status_code in (404, 410, 400, 402, 501):
                reason = f"unavailable ({r.status_code})"
                skipped.append({"path": path, "reason": reason})
                _log(
                    workspace,
                    "SKIP",
                    f"[{idx}/{total}] {path} -> HTTP {r.status_code} ({ms}ms) — "
                    f"best-effort endpoint, {reason}",
                )
            else:
                reason = f"HTTP {r.status_code}"
                skipped.append({"path": path, "reason": reason})
                _log(
                    workspace,
                    "WARN",
                    f"[{idx}/{total}] {path} -> {reason} ({ms}ms) — skipped",
                )

    meta = {
        "ref": project_ref,
        "name": name,
        "region": region,
        "status": status,
        "written": written,
        "skipped": skipped,
        "source": "supabase-management-api",
        "note": "Configuration + advisor metadata only. No SQL, no row data.",
    }
    _write_artifact(out_dir / "meta.json", meta)
    written.append("meta.json")

    total_ms = int((time.monotonic() - t_all) * 1000)
    skipped_paths = ", ".join(s["path"] for s in skipped) or "none"
    _log(
        workspace,
        "SUCCESS",
        f"snapshot DONE — name={name!r} ref={project_ref} "
        f"written={len(written)} skipped={len(skipped)} [{skipped_paths}] "
        f"durationMs={total_ms}",
    )
    return {"written": written, "skipped": skipped, "region": region}


def _write_artifact(path: Path, payload: Any) -> int:
    """Write a pretty-printed JSON artifact; returns the number of bytes written."""
    text = json.dumps(payload, indent=2, sort_keys=True)
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))
