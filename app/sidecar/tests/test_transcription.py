"""Audio transcription module: dedup, transcript output, no secret leakage."""
from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

from contextful_sidecar.runtime import transcription


class FakeSTTClient:
    """Records transcribe() calls and returns a canned transcript."""

    def __init__(self, text: str = "hello world transcript") -> None:
        self.text = text
        self.calls: list[dict[str, str | None]] = []

    async def transcribe(self, *, model, audio_b64, fmt, language=None):
        self.calls.append(
            {"model": model, "fmt": fmt, "language": language, "b64": audio_b64}
        )
        return self.text


def _make_audio(ws: Path, rel: str, data: bytes = b"\x00\x01RIFFfake-audio") -> Path:
    fp = ws / rel
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_bytes(data)
    return fp


def test_transcribe_writes_transcript_and_manifest(tmp_path: Path):
    ws = tmp_path / "project"
    _make_audio(ws, "meta/audio/clip.wav")
    client = FakeSTTClient("the quick brown fox")

    result = asyncio.run(
        transcription.transcribe_pending(
            workspace=ws, client=client, model="openai/whisper-large-v3"
        )
    )

    assert result["transcribed"] == ["meta/audio/clip.wav"]
    assert result["skipped"] == []
    assert result["failed"] == []

    transcript = ws / "meta" / "audio" / "clip.wav.transcript.md"
    assert transcript.exists()
    body = transcript.read_text(encoding="utf-8")
    assert "the quick brown fox" in body
    assert "meta/audio/clip.wav" in body  # source reference in header
    assert "openai/whisper-large-v3" in body  # model in header

    # format inferred from extension
    assert client.calls[0]["fmt"] == "wav"

    manifest = json.loads((ws / "meta" / ".transcripts.json").read_text())
    entry = manifest["entries"]["meta/audio/clip.wav"]
    assert entry["transcriptPath"] == "meta/audio/clip.wav.transcript.md"
    assert entry["model"] == "openai/whisper-large-v3"
    assert entry["contentHash"]


def test_dedup_skips_when_hash_unchanged(tmp_path: Path):
    ws = tmp_path / "project"
    _make_audio(ws, "meta/audio/clip.mp3")
    client = FakeSTTClient()

    first = asyncio.run(
        transcription.transcribe_pending(workspace=ws, client=client, model="m")
    )
    assert first["transcribed"] == ["meta/audio/clip.mp3"]
    assert len(client.calls) == 1

    second = asyncio.run(
        transcription.transcribe_pending(workspace=ws, client=client, model="m")
    )
    assert second["transcribed"] == []
    assert [s["path"] for s in second["skipped"]] == ["meta/audio/clip.mp3"]
    # No second network call — dedup short-circuits before transcribe()
    assert len(client.calls) == 1


def test_changed_audio_retranscribes(tmp_path: Path):
    ws = tmp_path / "project"
    fp = _make_audio(ws, "meta/audio/clip.m4a", b"original")
    client = FakeSTTClient()

    asyncio.run(transcription.transcribe_pending(workspace=ws, client=client, model="m"))
    assert len(client.calls) == 1

    fp.write_bytes(b"new-different-content")
    again = asyncio.run(
        transcription.transcribe_pending(workspace=ws, client=client, model="m")
    )
    assert again["transcribed"] == ["meta/audio/clip.m4a"]
    assert len(client.calls) == 2


def test_no_audio_or_key_in_eventlog(tmp_path: Path):
    ws = tmp_path / "project"
    secret_bytes = b"TOP-SECRET-AUDIO-PAYLOAD-1234567890"
    _make_audio(ws, "meta/audio/secret.flac", secret_bytes)
    client = FakeSTTClient()

    asyncio.run(transcription.transcribe_pending(workspace=ws, client=client, model="m"))

    eventlog = (ws / ".eventlog").read_text()
    # verbose trail present
    assert "transcription START" in eventlog
    assert "transcription SUCCESS" in eventlog
    assert "meta/audio/secret.flac" in eventlog
    # raw audio bytes never logged, in any encoding
    assert "TOP-SECRET-AUDIO-PAYLOAD" not in eventlog
    assert base64.b64encode(secret_bytes).decode("ascii") not in eventlog


def test_list_audio_reports_status(tmp_path: Path):
    ws = tmp_path / "project"
    _make_audio(ws, "meta/audio/a.wav")
    _make_audio(ws, "meta/audio/b.ogg")
    client = FakeSTTClient()

    # transcribe only a.wav by removing b temporarily? Simpler: transcribe all,
    # then assert both report transcribed.
    asyncio.run(transcription.transcribe_pending(workspace=ws, client=client, model="m"))

    listing = transcription.list_audio(ws)
    by_path = {a["path"]: a for a in listing}
    assert set(by_path) == {"meta/audio/a.wav", "meta/audio/b.ogg"}
    assert all(a["transcribed"] for a in listing)
    assert by_path["meta/audio/a.wav"]["transcriptPath"] == "meta/audio/a.wav.transcript.md"
