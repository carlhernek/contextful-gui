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
from pathlib import Path
from typing import Any

import certifi
import httpx

MGMT_BASE = "https://api.supabase.com/v1"
_TIMEOUT = 30


def _headers(pat: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {pat.strip()}",
        "Accept": "application/json",
        "User-Agent": "Contextful",
    }


async def list_projects(pat: str) -> list[dict[str, Any]]:
    """GET /v1/projects -> [{ ref, name, region, status, created_at }]."""
    pat = (pat or "").strip()
    if not pat:
        raise ValueError("missing Supabase personal access token")
    async with httpx.AsyncClient(verify=certifi.where(), timeout=_TIMEOUT) as c:
        r = await c.get(f"{MGMT_BASE}/projects", headers=_headers(pat))
        if r.status_code in (401, 403):
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
        raise ValueError("missing Supabase personal access token")
    if not project_ref:
        raise ValueError("missing project ref")

    out_dir = Path(workspace) / "supabase" / subdir
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    skipped: list[dict[str, str]] = []
    headers = _headers(pat)

    async with httpx.AsyncClient(verify=certifi.where(), timeout=_TIMEOUT) as c:
        for filename, path in _SNAPSHOT_ENDPOINTS:
            url = f"{MGMT_BASE}/projects/{project_ref}/{path}"
            try:
                r = await c.get(url, headers=headers)
            except httpx.HTTPError as exc:
                skipped.append({"path": path, "reason": f"request error: {exc}"})
                continue
            if r.status_code == 200:
                try:
                    body = r.json()
                except ValueError:
                    skipped.append({"path": path, "reason": "non-JSON response"})
                    continue
                if path == "api-keys":
                    body = _redact_api_keys(body)
                _write_artifact(out_dir / filename, body)
                written.append(filename)
            elif r.status_code in (401, 403):
                raise RuntimeError(
                    f"Supabase rejected the token for {path} ({r.status_code})."
                )
            elif path in _BEST_EFFORT and r.status_code in (404, 410, 400, 402, 501):
                skipped.append({"path": path, "reason": f"unavailable ({r.status_code})"})
            else:
                skipped.append({"path": path, "reason": f"HTTP {r.status_code}"})

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

    return {"written": written, "skipped": skipped, "region": region}


def _write_artifact(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
