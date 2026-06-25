"""Supabase Management API client: project list + snapshot + advisor fallback."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from contextful_sidecar.runtime import supabase


def _resp(status: int, payload=None) -> httpx.Response:
    request = httpx.Request("GET", "https://api.supabase.com/v1/x")
    if payload is None:
        return httpx.Response(status, request=request)
    return httpx.Response(status, request=request, json=payload)


class _FakeClient:
    """Routes GET calls by URL suffix to a provided response map."""

    def __init__(self, routes: dict[str, httpx.Response]) -> None:
        self._routes = routes
        self.calls: list[str] = []

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url: str, headers=None):
        self.calls.append(url)
        for suffix, resp in self._routes.items():
            if url.endswith(suffix):
                return resp
        return _resp(404)


def test_list_projects_maps_fields(monkeypatch):
    fake = _FakeClient(
        {
            "/projects": _resp(
                200,
                [
                    {
                        "id": "abc123",
                        "name": "Prod",
                        "region": "eu-central-1",
                        "status": "ACTIVE_HEALTHY",
                    }
                ],
            )
        }
    )
    monkeypatch.setattr(supabase.httpx, "AsyncClient", fake)
    projects = asyncio.run(supabase.list_projects("sbp_token"))
    assert projects == [
        {
            "ref": "abc123",
            "name": "Prod",
            "region": "eu-central-1",
            "status": "ACTIVE_HEALTHY",
            "created_at": None,
            "organization_id": None,
        }
    ]


def test_snapshot_writes_artifacts_and_skips_missing_advisors(monkeypatch, tmp_path: Path):
    routes = {
        "config/auth": _resp(200, {"disable_signup": False, "jwt_exp": 3600}),
        "postgrest": _resp(200, {"db_schema": "public", "max_rows": 1000}),
        "config/storage": _resp(200, {"fileSizeLimit": 52428800}),
        "functions": _resp(200, [{"slug": "hello", "verify_jwt": True}]),
        "config/database/postgres": _resp(200, {"effective_cache_size": "x"}),
        "config/database/ssl-enforcement": _resp(200, {"appliedSuccessfully": True}),
        "config/database/pooler": _resp(200, {"pool_mode": "transaction"}),
        "network-restrictions": _resp(200, {"config": {"dbAllowedCidrs": ["0.0.0.0/0"]}}),
        "api-keys": _resp(
            200,
            [
                {"name": "anon", "type": "anon", "id": "k1", "api_key": "secret-value"},
                {"name": "service_role", "type": "service_role", "id": "k2", "api_key": "x"},
            ],
        ),
        # advisors deprecated -> 410, must be skipped gracefully
        "advisors/security": _resp(410),
        "advisors/performance": _resp(404),
    }
    fake = _FakeClient(routes)
    monkeypatch.setattr(supabase.httpx, "AsyncClient", fake)

    result = asyncio.run(
        supabase.snapshot(
            pat="sbp_token",
            project_ref="abc123",
            name="Prod",
            region="eu-central-1",
            status="ACTIVE_HEALTHY",
            workspace=str(tmp_path),
            subdir="prod",
        )
    )

    out = tmp_path / "supabase" / "prod"
    assert (out / "auth.json").exists()
    assert (out / "meta.json").exists()
    assert "advisors_security.json" not in result["written"]
    assert any(s["path"] == "advisors/security" for s in result["skipped"])

    # api keys redacted: presence kept, secret stripped
    keys = json.loads((out / "api_keys.json").read_text())
    assert keys[0] == {"name": "anon", "type": "anon", "id": "k1", "present": True}
    assert "api_key" not in keys[0]

    meta = json.loads((out / "meta.json").read_text())
    assert meta["ref"] == "abc123"
    assert meta["region"] == "eu-central-1"
    assert "auth.json" in meta["written"]

    # Ultra-verbose logging: snapshot writes a START + per-endpoint + SUCCESS
    # trail to the workspace .eventlog, and never leaks the token value.
    eventlog = (tmp_path / ".eventlog").read_text()
    assert "supabase START" in eventlog
    assert "snapshot DONE" in eventlog
    assert "config/auth" in eventlog
    assert "api keys redacted" in eventlog
    assert "sbp_token" not in eventlog  # PAT must never be logged


def test_list_projects_logs_without_leaking_token(monkeypatch, tmp_path: Path):
    fake = _FakeClient({"/projects": _resp(200, [{"id": "abc123", "name": "Prod"}])})
    monkeypatch.setattr(supabase.httpx, "AsyncClient", fake)
    asyncio.run(supabase.list_projects("sbp_secret", workspace=str(tmp_path)))
    eventlog = (tmp_path / ".eventlog").read_text()
    assert "list_projects" in eventlog
    assert "1 project(s) returned" in eventlog
    assert "sbp_secret" not in eventlog  # PAT must never be logged


def test_list_projects_rejects_bad_token(monkeypatch):
    fake = _FakeClient({"/projects": _resp(401)})
    monkeypatch.setattr(supabase.httpx, "AsyncClient", fake)
    try:
        asyncio.run(supabase.list_projects("bad"))
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "rejected the token" in str(exc)
