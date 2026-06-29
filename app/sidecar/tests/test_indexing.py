"""Tests for workspace indexing module."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from contextful_sidecar.runtime.indexing import (
    ANNOTATIONS_FILE,
    CACHE_FILE,
    INDEX_FILE,
    _file_content_hash,
    build_index_document,
    format_index_for_prompt,
    refresh_index,
    scan_items,
)
from contextful_sidecar.runtime.tools import execute_readonly_tool


def test_scan_items_includes_supabase_snapshots(tmp_path: Path):
    ws = tmp_path / "project"
    ws.mkdir()
    (ws / ".contextful.json").write_text(
        json.dumps({"display_name": "demo", "project_type": "both", "repos": []}),
        encoding="utf-8",
    )
    sub = ws / "supabase" / "prod"
    sub.mkdir(parents=True)
    (sub / "meta.json").write_text(json.dumps({"ref": "abc", "region": "eu-central-1"}), encoding="utf-8")
    (sub / "advisors_security.json").write_text(json.dumps({"lints": []}), encoding="utf-8")

    items = scan_items(ws, include_artifacts=False)
    sb = [i for i in items if i["type"] == "supabase"]
    ids = {i["id"] for i in sb}
    assert ids == {"supabase:prod/advisors_security.json", "supabase:prod/meta.json"}
    assert sb[0]["meta"]["connection"] == "prod"

    doc = build_index_document(ws, [
        {**i, "description": "", "keywords": []} for i in sb
    ])
    prompt = format_index_for_prompt(doc)
    assert "Supabase config snapshots:" in prompt


def test_raw_audio_excluded_but_transcript_indexed(tmp_path: Path):
    ws = tmp_path / "project"
    ws.mkdir()
    (ws / ".contextful.json").write_text(
        json.dumps({"display_name": "demo", "project_type": "both", "repos": []}),
        encoding="utf-8",
    )
    audio_dir = ws / "meta" / "audio"
    audio_dir.mkdir(parents=True)
    # raw audio should never become an index item (it's binary)
    (audio_dir / "clip.mp3").write_bytes(b"\x00\x01fake-audio-bytes")
    # its transcript is a normal meta document and must be indexed
    (audio_dir / "clip.mp3.transcript.md").write_text(
        "# Transcript: clip.mp3\n\nhello world\n", encoding="utf-8"
    )

    items = scan_items(ws, include_artifacts=False)
    meta_ids = {i["id"] for i in items if i["type"] == "meta"}
    assert "meta:audio/clip.mp3" not in meta_ids
    assert "meta:audio/clip.mp3.transcript.md" in meta_ids


class FakeClient:
    async def chat_completion(self, *, model, messages, tools=None, on_token=None):
        _ = model, tools, on_token
        user = messages[-1]["content"]
        if "backoffice" in user.lower():
            desc = "Admin backoffice service"
            kws = ["backoffice", "admin", "orders"]
        else:
            desc = "Indexed workspace item"
            kws = ["workspace", "item"]
        return {
            "choices": [{
                "message": {
                    "content": json.dumps({"description": desc, "keywords": kws}),
                },
            }],
        }


def _write_meta(workspace: Path, repos=None):
    meta = {
        "display_name": "demo",
        "project_type": "both",
        "repos": repos or [{"name": "backoffice", "url": "git@x/backoffice.git", "branch": "develop"}],
    }
    (workspace / ".contextful.json").write_text(json.dumps(meta), encoding="utf-8")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "project"
    ws.mkdir()
    _write_meta(ws)
    repo = ws / "repos" / "backoffice"
    repo.mkdir(parents=True)
    (repo / "README.md").write_text("# Backoffice\nOrder admin API\n", encoding="utf-8")
    meta = ws / "meta"
    meta.mkdir()
    (meta / "requirements.md").write_text("# Requirements\nUser stories\n", encoding="utf-8")
    run = ws / "runs" / "20260101-abc1" / "tech-debt"
    run.mkdir(parents=True)
    (run / "analysis.md").write_text("# Tech debt analysis\nFindings here\n", encoding="utf-8")
    return ws


def test_scan_items_finds_repo_meta_artifact(workspace: Path):
    items = scan_items(workspace)
    ids = {i["id"] for i in items}
    assert "repo:backoffice" in ids
    assert "meta:requirements.md" in ids
    assert "artifact:20260101-abc1/tech-debt/analysis.md" in ids


def test_scan_items_binary_meta_uses_stat_hash(workspace: Path, monkeypatch):
    docx = workspace / "meta" / "notes.docx"
    docx.write_bytes(b"PK" + b"\x00" * 5000)
    read_sizes: list[int] = []
    original_read = Path.read_bytes

    def tracked_read(self, *args, **kwargs):
        read_sizes.append(1)
        return original_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", tracked_read)
    h1 = _file_content_hash(docx)
    h2 = _file_content_hash(docx)
    assert h1 == h2
    assert read_sizes == []
    items = scan_items(workspace)
    meta = next(i for i in items if i["id"] == "meta:notes.docx")
    assert meta["contentHash"] == h1
    assert "binary file" in meta["snippet"]


def test_refresh_index_enriches_and_caches(workspace: Path):
    client = FakeClient()

    async def run():
        result = await refresh_index(workspace=workspace, client=client, models={"module": "test/model"})
        assert result["ok"] is True
        assert result["itemCount"] >= 3
        assert result["enriched"] >= 1

        index = json.loads((workspace / INDEX_FILE).read_text(encoding="utf-8"))
        repo = next(i for i in index["items"] if i["id"] == "repo:backoffice")
        assert "backoffice" in repo["description"].lower() or repo["source"] in ("ai", "heuristic")

        result2 = await refresh_index(workspace=workspace, client=client, models={"module": "test/model"})
        assert result2["enriched"] == 0

    asyncio.run(run())


def test_user_override_from_index_file_beats_ai(workspace: Path):
    (workspace / INDEX_FILE).write_text(
        json.dumps({
            "version": 1,
            "items": [{
                "id": "repo:backoffice",
                "type": "repo",
                "path": "repos/backoffice",
                "description": "GUI edit",
                "keywords": ["manual"],
                "source": "user",
                "userEdited": True,
            }],
        }),
        encoding="utf-8",
    )
    client = FakeClient()

    async def run():
        await refresh_index(workspace=workspace, client=client, models={"module": "test/model"})
        index = json.loads((workspace / INDEX_FILE).read_text(encoding="utf-8"))
        repo = next(i for i in index["items"] if i["id"] == "repo:backoffice")
        assert repo["description"] == "GUI edit"
        assert repo["keywords"] == ["manual"]
        assert repo["source"] == "user"

    asyncio.run(run())


def test_user_override_beats_ai(workspace: Path):
    ann_path = workspace / ANNOTATIONS_FILE
    ann_path.write_text(
        json.dumps({"version": 1, "items": {"repo:backoffice": {"description": "User label", "keywords": ["custom"]}}}),
        encoding="utf-8",
    )
    client = FakeClient()

    async def run():
        await refresh_index(workspace=workspace, client=client, models={"module": "test/model"})
        index = json.loads((workspace / INDEX_FILE).read_text(encoding="utf-8"))
        repo = next(i for i in index["items"] if i["id"] == "repo:backoffice")
        assert repo["description"] == "User label"
        assert repo["keywords"] == ["custom"]
        assert repo["source"] == "user"

    asyncio.run(run())


def test_format_index_for_prompt_includes_repo(workspace: Path):
    doc = build_index_document(workspace, [{
        "id": "repo:backoffice",
        "type": "repo",
        "path": "repos/backoffice",
        "description": "Admin service",
        "keywords": ["backoffice"],
        "source": "ai",
        "meta": {"cloned": True, "head": "abc1234"},
        "entries": [],
        "contentHash": "x",
        "enrichedAt": None,
    }])
    text = format_index_for_prompt(doc)
    assert "repo:backoffice" in text
    assert "Admin service" in text


def test_item_timestamps_on_refresh(workspace: Path, monkeypatch):
    times = iter([
        "2026-06-01T10:00:00+02:00",
        "2026-06-01T10:00:00+02:00",
        "2026-06-01T10:00:00+02:00",
        "2026-06-01T11:00:00+02:00",
    ])

    def fake_now():
        return next(times, "2026-06-01T12:00:00+02:00")

    monkeypatch.setattr(
        "contextful_sidecar.runtime.indexing._now_iso",
        fake_now,
    )

    async def run():
        await refresh_index(workspace=workspace, client=None, skip_enrichment=True)
        index1 = json.loads((workspace / INDEX_FILE).read_text(encoding="utf-8"))
        meta = next(i for i in index1["items"] if i["id"] == "meta:requirements.md")
        assert meta.get("indexedAt")
        assert meta.get("contentUpdatedAt") == meta["indexedAt"]
        first_indexed = meta["indexedAt"]
        first_updated = meta["contentUpdatedAt"]

        await refresh_index(workspace=workspace, client=None, skip_enrichment=True)
        index2 = json.loads((workspace / INDEX_FILE).read_text(encoding="utf-8"))
        meta2 = next(i for i in index2["items"] if i["id"] == "meta:requirements.md")
        assert meta2["indexedAt"] == first_indexed
        assert meta2["contentUpdatedAt"] == first_updated

        (workspace / "meta" / "requirements.md").write_text("# Requirements\nUpdated content\n", encoding="utf-8")
        await refresh_index(workspace=workspace, client=None, skip_enrichment=True)
        index3 = json.loads((workspace / INDEX_FILE).read_text(encoding="utf-8"))
        meta3 = next(i for i in index3["items"] if i["id"] == "meta:requirements.md")
        assert meta3["indexedAt"] == first_indexed
        assert meta3["contentUpdatedAt"] != first_updated

    asyncio.run(run())


def test_execute_readonly_tool_rejects_write(workspace: Path):
    result = execute_readonly_tool(workspace, "write_file", {"path": "x", "content": "y"})
    assert result.startswith("ERROR:")
    listing = execute_readonly_tool(workspace, "list_directory", {"path": "meta"})
    assert "requirements.md" in listing or "file" in listing


def test_skip_enrichment_merge_only(workspace: Path):
    async def run():
        await refresh_index(workspace=workspace, client=None, skip_enrichment=True)
        index = json.loads((workspace / INDEX_FILE).read_text(encoding="utf-8"))
        assert len(index["items"]) >= 1

    asyncio.run(run())


def test_force_enrich_bypasses_user_guard(workspace: Path):
    ann_path = workspace / ANNOTATIONS_FILE
    ann_path.write_text(
        json.dumps({"version": 1, "items": {"repo:backoffice": {"description": "User label", "keywords": ["custom"]}}}),
        encoding="utf-8",
    )
    client = FakeClient()

    async def run():
        await refresh_index(
            workspace=workspace,
            client=client,
            models={"module": "test/model"},
            force_item_ids=["repo:backoffice"],
            force_enrich=True,
        )
        index = json.loads((workspace / INDEX_FILE).read_text(encoding="utf-8"))
        repo = next(i for i in index["items"] if i["id"] == "repo:backoffice")
        assert repo["source"] == "ai"
        assert "admin" in repo["description"].lower() or "backoffice" in repo["description"].lower()

    asyncio.run(run())
