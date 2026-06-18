"""Sidecar RPC manifest — must stay in sync with lib.rs rpc() calls."""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from contextful_sidecar.server import SidecarServer

# Methods the Tauri layer invokes via SidecarManager::request (lib.rs).
REQUIRED_RPC_METHODS = frozenset({
    "cancel",
    "configure",
    "health",
    "list_models",
    "run_modules",
    "get_run_state",
    "chat",
    "preview",
    "refresh_index",
    "enrich_index_item",
})

# Implemented in server.py handle() dispatch (excluding cancel which is special).
IMPLEMENTED_METHODS = frozenset({
    "configure",
    "health",
    "list_models",
    "run_modules",
    "get_run_state",
    "chat",
    "preview",
    "refresh_index",
    "enrich_index_item",
})


def test_rust_required_methods_implemented():
    missing = REQUIRED_RPC_METHODS - IMPLEMENTED_METHODS - {"cancel"}
    assert not missing, f"sidecar missing RPC methods used by Rust: {sorted(missing)}"


def _seed_workspace(root: Path) -> Path:
    ws = root / "ws"
    ws.mkdir()
    (ws / ".contextful.json").write_text(
        json.dumps({
            "display_name": "rpc-test",
            "project_type": "both",
            "repos": [{"name": "web", "url": "u", "branch": "main"}],
        }),
        encoding="utf-8",
    )
    (ws / "repos" / "web").mkdir(parents=True)
    (ws / "repos" / "web" / "README.md").write_text("# Web\n", encoding="utf-8")
    (ws / "meta").mkdir()
    (ws / "modules" / "security-analysis").mkdir(parents=True)
    (ws / "runs" / "r1" / "security-analysis").mkdir(parents=True)
    (ws / "runs" / "r1" / "security-analysis" / "analysis.md").write_text("# a", encoding="utf-8")
    return ws


def _assert_not_unknown(resp: dict) -> None:
    err = resp.get("error", "")
    assert "unknown method" not in str(err).lower(), f"unexpected unknown method: {resp}"


def test_index_rpc_methods_dispatch():
    async def run():
        srv = SidecarServer()
        await srv.handle({"id": "c", "method": "configure", "params": {"api_key": "fake-key"}})
        with tempfile.TemporaryDirectory() as tmp:
            ws = _seed_workspace(Path(tmp))
            ws_str = str(ws)

            refresh = await srv.handle({
                "id": "r1",
                "method": "refresh_index",
                "params": {"workspace": ws_str, "skipEnrichment": True},
            })
            _assert_not_unknown(refresh)
            assert refresh.get("result", {}).get("ok") is True
            assert (ws / ".workspace-index.json").exists()

            enrich = await srv.handle({
                "id": "e1",
                "method": "enrich_index_item",
                "params": {"workspace": ws_str, "itemId": "repo:web"},
            })
            _assert_not_unknown(enrich)

            preview = await srv.handle({
                "id": "p1",
                "method": "preview",
                "params": {"workspace": ws_str, "path": "README.md", "base": "repos/web"},
            })
            _assert_not_unknown(preview)
            assert preview.get("result", {}).get("ok") is True

            state = await srv.handle({
                "id": "s1",
                "method": "get_run_state",
                "params": {"workspace": ws_str, "runId": "r1"},
            })
            _assert_not_unknown(state)
            assert state.get("result", {}).get("runId") == "r1"

    asyncio.run(run())
