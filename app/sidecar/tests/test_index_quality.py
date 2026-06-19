"""Tests for index enrichment parsing and quality helpers."""
from contextful_sidecar.runtime.indexing import (
    HEURISTIC_BINARY_DESC_RE,
    HEURISTIC_REPO_DESC_RE,
    _looks_heuristic_cached,
    _parse_enrichment,
)


def test_parse_enrichment_clean_json():
    out = _parse_enrichment('{"description": "Flutter guest app", "keywords": ["flutter"]}')
    assert out["description"] == "Flutter guest app"
    assert out["keywords"] == ["flutter"]


def test_parse_enrichment_prose_then_json():
    text = (
        "The .docx file is binary.\n\n"
        '{"description": "Meeting notes for Speedbox intern standup on June 18, 2026", '
        '"keywords": ["speedbox", "intern", "standup"]}'
    )
    out = _parse_enrichment(text)
    assert "Meeting notes" in out["description"]
    assert "speedbox" in out["keywords"]


def test_parse_enrichment_fenced_json():
    text = '```json\n{"description": "API backend", "keywords": ["rust"]}\n```'
    out = _parse_enrichment(text)
    assert out["description"] == "API backend"


def test_heuristic_cached_detection():
    assert HEURISTIC_REPO_DESC_RE.match("Repository guest-app @ 401a20ac9fa3")
    assert HEURISTIC_BINARY_DESC_RE.match("binary file (15848 bytes)")
    assert _looks_heuristic_cached({"description": "Repository x @ abcdef123456", "source": "ai"})
    assert not _looks_heuristic_cached({"description": "Flutter mobile app", "source": "ai"})
