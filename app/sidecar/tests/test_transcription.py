"""Audio transcription module: dedup, transcript output, no secret leakage."""
from __future__ import annotations

import asyncio
import base64
import json
import wave
from pathlib import Path

import pytest

from contextful_sidecar.runtime import guard, transcription


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


def _make_wav(fp: Path, *, frames: int, framerate: int = 8000) -> None:
    """Write a mono 16-bit PCM WAV with the given number of frames."""
    fp.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(fp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(framerate)
        w.writeframes(b"\x01\x02" * frames)


def test_large_wav_is_chunked_and_joined(tmp_path: Path, monkeypatch):
    # Shrink the cap/chunk budget so a tiny WAV exercises the split path.
    monkeypatch.setattr(transcription, "MAX_AUDIO_BYTES", 1000)
    monkeypatch.setattr(transcription, "_CHUNK_TARGET_BYTES", 400)

    ws = tmp_path / "project"
    rel = "meta/audio/big.wav"
    fp = ws / rel
    _make_wav(fp, frames=4000)  # 8000 data bytes -> well over the 1000-byte cap
    assert fp.stat().st_size > transcription.MAX_AUDIO_BYTES

    client = FakeSTTClient("part")
    result = asyncio.run(
        transcription.transcribe_pending(workspace=ws, client=client, model="m")
    )

    assert result["transcribed"] == [rel]
    assert result["failed"] == []
    # Split into multiple sub-cap chunks, each uploaded as wav, each under the cap.
    assert len(client.calls) > 1
    assert all(c["fmt"] == "wav" for c in client.calls)
    for c in client.calls:
        assert len(base64.b64decode(c["b64"])) <= transcription.MAX_AUDIO_BYTES

    transcript = (ws / "meta/audio/big.wav.transcript.md").read_text(encoding="utf-8")
    # One "part" per chunk, joined in order.
    assert transcript.count("part") == len(client.calls)


def test_oversized_non_wav_reports_clear_reason(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(transcription, "MAX_AUDIO_BYTES", 10)
    ws = tmp_path / "project"
    _make_audio(ws, "meta/audio/big.mp3", b"x" * 100)
    client = FakeSTTClient()

    result = asyncio.run(
        transcription.transcribe_pending(workspace=ws, client=client, model="m")
    )

    assert result["transcribed"] == []
    assert len(result["failed"]) == 1
    assert "convert to WAV" in result["failed"][0]["reason"]
    assert client.calls == []  # never uploaded


class HangThenSkipClient:
    """Hangs (until the guard times out) on audio containing a marker; else ok."""

    def __init__(self, hang_marker: bytes, text: str = "good") -> None:
        self.hang_marker = hang_marker
        self.text = text
        self.calls: list[str] = []

    async def transcribe(self, *, model, audio_b64, fmt, language=None):
        self.calls.append(fmt)
        if self.hang_marker in base64.b64decode(audio_b64):
            await asyncio.sleep(10)  # cut short by the wall-clock guard
        return self.text


def _fast_guard(monkeypatch, timeout_sec: float = 0.05) -> None:
    """Force transcription's guard to a tiny timeout with no retry backoff."""
    real = guard.run_guarded

    async def fast(factory, **kw):
        kw["timeout_sec"] = timeout_sec
        return await real(factory, **kw)

    monkeypatch.setattr(transcription, "run_guarded", fast)
    monkeypatch.setattr(guard, "GUARD_RETRY_BASE_DELAY_SEC", 0.0)
    monkeypatch.setattr(guard, "GUARD_RETRY_MAX_DELAY_SEC", 0.0)


def test_hung_file_is_skipped_and_others_continue(tmp_path: Path, monkeypatch):
    _fast_guard(monkeypatch)
    ws = tmp_path / "project"
    marker = b"\xaa\xbb\xaa\xbb"
    # a_hang sorts first and contains the marker so its STT call always hangs.
    _make_audio(ws, "meta/audio/a_hang.wav", b"RIFFhead" + marker * 4)
    _make_audio(ws, "meta/audio/b_good.wav", b"RIFFhead-clean-bytes")

    client = HangThenSkipClient(marker)
    result = asyncio.run(
        transcription.transcribe_pending(workspace=ws, client=client, model="m")
    )

    # The hung file exhausts retries and is skipped; the good file still runs.
    assert result["transcribed"] == ["meta/audio/b_good.wav"]
    assert [f["path"] for f in result["failed"]] == ["meta/audio/a_hang.wav"]
    assert "timed out" in result["failed"][0]["reason"]

    log = (ws / ".eventlog").read_text()
    assert "transcription TIMEOUT" in log
    assert "transcription RETRY" in log


def test_cancel_between_chunks_stops_cleanly(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(transcription, "MAX_AUDIO_BYTES", 1000)
    monkeypatch.setattr(transcription, "_CHUNK_TARGET_BYTES", 400)
    ws = tmp_path / "project"
    _make_wav(ws / "meta/audio/big.wav", frames=4000)

    checks = {"n": 0}

    def should_cancel() -> bool:
        checks["n"] += 1
        # Allow the first chunk, then request cancel before the second.
        return checks["n"] >= 3

    client = FakeSTTClient("part")
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            transcription.transcribe_pending(
                workspace=ws, client=client, model="m", should_cancel=should_cancel
            )
        )
    # Stopped mid-file, not all chunks transcribed.
    assert len(client.calls) >= 1


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
