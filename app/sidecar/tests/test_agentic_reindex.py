"""Agentic workspace indexing orchestration."""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

from contextful_sidecar.runtime.activity import read_activity
from contextful_sidecar.runtime.indexing import INDEX_FILE, agentic_reindex
from contextful_sidecar.runtime.index_agent import MODULE_ID


def _fixture_ws(tmp: str) -> Path:
    ws = Path(tmp) / "ws"
    ws.mkdir()
    (ws / ".contextful.json").write_text(
        json.dumps({
            "display_name": "x",
            "project_type": "both",
            "repos": [{"name": "web", "url": "u", "branch": "main"}],
        }),
        encoding="utf-8",
    )
    (ws / "repos" / "web").mkdir(parents=True)
    (ws / "repos" / "web" / "README.md").write_text("# Web\n", encoding="utf-8")
    (ws / "meta").mkdir()
    (ws / "meta" / "notes.md").write_text("# Notes\n", encoding="utf-8")
    return ws


def test_agentic_reindex_enumerates_and_enriches():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            ws = _fixture_ws(tmp)
            client = AsyncMock()
            client.chat_completion.return_value = {
                "choices": [{
                    "message": {
                        "content": '{"description":"indexed item","keywords":["idx"]}',
                    },
                }],
            }
            events: list[tuple[str, object]] = []

            result = await agentic_reindex(
                workspace=ws,
                run_id="idx-run",
                client=client,
                models={"module": "test/model"},
                on_event=lambda ev, data: events.append((ev, data)),
            )
            assert result["itemCount"] >= 2
            assert result["enriched"] >= 1
            index = json.loads((ws / INDEX_FILE).read_text(encoding="utf-8"))
            assert any(i.get("status") == "done" for i in index["items"])
            log = (ws / ".eventlog").read_text(encoding="utf-8")
            assert any(ev == "index" and (d := data).get("phase") == "enumerate" for ev, data in events)
            assert "SCAN_START" in log
            assert "SCAN_DONE" in log
            activity = read_activity(ws, "idx-run", MODULE_ID)
            assert any(a["kind"] == "item" for a in activity)

    asyncio.run(run())


def test_agentic_reindex_skips_cached_items():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            ws = _fixture_ws(tmp)
            from contextful_sidecar.runtime.indexing import CACHE_FILE
            import hashlib

            notes_bytes = (ws / "meta" / "notes.md").read_bytes()
            cache = {
                "meta:notes.md": {
                    "contentHash": hashlib.sha1(notes_bytes).hexdigest(),
                    "description": "existing",
                    "keywords": ["keep"],
                    "source": "ai",
                },
            }
            (ws / CACHE_FILE).write_text(json.dumps(cache), encoding="utf-8")

            client = AsyncMock()
            client.chat_completion.return_value = {
                "choices": [{
                    "message": {"content": '{"description":"new","keywords":["x"]}'},
                }],
            }
            result = await agentic_reindex(
                workspace=ws,
                run_id="skip-run",
                client=client,
                models={"module": "test/model"},
            )
            assert result["skipped"] >= 1
            log = (ws / ".eventlog").read_text(encoding="utf-8")
            assert "CACHE_HIT" in log
            index = json.loads((ws / INDEX_FILE).read_text(encoding="utf-8"))
            notes = next(i for i in index["items"] if i["id"] == "meta:notes.md")
            assert notes["description"] == "existing"
            assert notes["status"] == "cached"

    asyncio.run(run())


def test_agentic_reindex_force_reindexes_cached_items():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            ws = _fixture_ws(tmp)
            from contextful_sidecar.runtime.indexing import CACHE_FILE, _sha1

            notes_bytes = (ws / "meta" / "notes.md").read_bytes()
            cache = {
                "meta:notes.md": {
                    "contentHash": _sha1(notes_bytes),
                    "description": "existing",
                    "keywords": ["keep"],
                    "source": "ai",
                },
            }
            (ws / CACHE_FILE).write_text(json.dumps(cache), encoding="utf-8")

            client = AsyncMock()
            client.chat_completion.return_value = {
                "choices": [{
                    "message": {"content": '{"description":"reindexed","keywords":["new"]}'},
                    "finish_reason": "stop",
                }],
            }
            result = await agentic_reindex(
                workspace=ws,
                run_id="force-run",
                client=client,
                models={"module": "test/model"},
                force_reindex=True,
            )
            assert result["enriched"] >= 1
            log = (ws / ".eventlog").read_text(encoding="utf-8")
            assert "FORCE_REINDEX" in log
            index = json.loads((ws / INDEX_FILE).read_text(encoding="utf-8"))
            notes = next(i for i in index["items"] if i["id"] == "meta:notes.md")
            assert notes["description"] == "reindexed"

    asyncio.run(run())
