"""Audio transcription for meta documents (OpenRouter STT).

Audio files live under ``meta/`` like any other meta document. This module
transcribes only audio that has not been processed before (tracked by content
hash in ``meta/.transcripts.json``), writes each transcript as a sibling
``<name>.transcript.md`` text meta document (which the indexer then picks up),
and leaves the raw audio out of the index in favor of the transcript.

The audio bytes and the OpenRouter API key are never logged.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from contextful_sidecar.runtime.file_text import AUDIO_EXTENSIONS
from contextful_sidecar.runtime.guard import run_guarded
from contextful_sidecar.runtime.openrouter import OpenRouterClient
from contextful_sidecar.runtime.step_log import log_step

EventCallback = Callable[[str, Any], None]
CancelCheck = Callable[[], bool]

_SCOPE = "transcription"
MANIFEST_REL = "meta/.transcripts.json"
MANIFEST_VERSION = 1
TRANSCRIPT_SUFFIX = ".transcript.md"
# Most STT providers cap upload size; guard well under typical limits. Oversized
# WAV files are split into sub-cap segments and transcribed chunk-by-chunk (see
# _iter_wav_chunks); other oversized formats are reported instead.
MAX_AUDIO_BYTES = 24 * 1024 * 1024
# Per-chunk raw audio budget. Kept at 3/4 of the cap so the base64-encoded
# upload (~4/3 larger) also stays under MAX_AUDIO_BYTES.
_CHUNK_TARGET_BYTES = (MAX_AUDIO_BYTES * 3) // 4
_HASH_CHUNK = 1024 * 1024


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _log(workspace: Path, status: str, message: str) -> None:
    try:
        log_step(Path(workspace), scope=_SCOPE, status=status, message=message)
    except Exception:  # noqa: BLE001 — logging is best-effort only
        pass


def _file_hash(fp: Path) -> str:
    h = hashlib.sha1()
    with fp.open("rb") as f:
        while chunk := f.read(_HASH_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _audio_format(fp: Path) -> str:
    return fp.suffix.lower().lstrip(".")


def _iter_wav_chunks(fp: Path, target_data_bytes: int) -> Iterator[bytes]:
    """Yield standalone WAV blobs, each holding a slice of the source WAV.

    Splits strictly on frame boundaries so every emitted blob is an independently
    decodable WAV (correct header + a contiguous run of PCM frames). Used to keep
    each STT upload under the provider size cap without any external tooling.
    """
    with wave.open(str(fp), "rb") as src:
        nchannels = src.getnchannels()
        sampwidth = src.getsampwidth()
        framerate = src.getframerate()
        bytes_per_frame = max(1, nchannels * sampwidth)
        frames_per_chunk = max(1, target_data_bytes // bytes_per_frame)
        while True:
            frames = src.readframes(frames_per_chunk)
            if not frames:
                break
            buf = io.BytesIO()
            with wave.open(buf, "wb") as out:
                out.setnchannels(nchannels)
                out.setsampwidth(sampwidth)
                out.setframerate(framerate)
                out.writeframes(frames)
            yield buf.getvalue()


async def _transcribe_wav_in_chunks(
    fp: Path,
    *,
    client: OpenRouterClient,
    model: str,
    language: str | None,
    workspace: Path,
    rel: str,
    on_event: EventCallback,
    should_cancel: CancelCheck | None = None,
) -> str:
    """Transcribe an oversized WAV by splitting it into sub-cap segments.

    Each segment is transcribed independently (under the shared timeout+retry
    guard) and the texts are concatenated in order. A per-chunk progress event
    (and a heartbeat on each retry) is emitted so the outer watchdog sees
    liveness during long multi-chunk files. Raises if any segment fails so the
    caller marks the file failed.
    """
    chunks = list(_iter_wav_chunks(fp, _CHUNK_TARGET_BYTES))
    total = len(chunks)
    _log(workspace, "REQUEST", f"{rel} split into {total} chunk(s) for transcription")
    parts: list[str] = []
    for idx, blob in enumerate(chunks, start=1):
        if should_cancel and should_cancel():
            raise asyncio.CancelledError()
        audio_b64 = base64.b64encode(blob).decode("ascii")
        _log(workspace, "REQUEST", f"{rel} chunk {idx}/{total} -> STT bytes={len(blob)}")
        on_event("transcribe", {"path": rel, "status": "chunk", "chunk": idx, "total": total})
        text = await run_guarded(
            lambda b64=audio_b64: client.transcribe(
                model=model, audio_b64=b64, fmt="wav", language=language
            ),
            label=f"transcribe {rel} chunk {idx}/{total}",
            scope=_SCOPE,
            workspace=workspace,
            heartbeat=lambda m: on_event(
                "transcribe", {"path": rel, "status": "retry", "chunk": idx, "detail": m}
            ),
        )
        parts.append(text.strip())
        _log(
            workspace,
            "WRITE",
            f"{rel} chunk {idx}/{total} transcribed ({len(text)} chars)",
        )
    return "\n\n".join(p for p in parts if p)


def _iter_audio_files(meta_dir: Path) -> list[Path]:
    """Stack walk of meta/ for audio files (skips dot/heavy dirs and transcripts)."""
    out: list[Path] = []
    if not meta_dir.is_dir():
        return out
    stack = [meta_dir]
    while stack:
        current = stack.pop()
        try:
            children = sorted(current.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for child in children:
            if child.name.startswith("."):
                continue
            if child.is_dir():
                stack.append(child)
            elif child.is_file() and child.suffix.lower() in AUDIO_EXTENSIONS:
                out.append(child)
    return sorted(out, key=lambda p: p.as_posix().lower())


def _manifest_path(workspace: Path) -> Path:
    return Path(workspace) / MANIFEST_REL


def _load_manifest(workspace: Path) -> dict[str, Any]:
    path = _manifest_path(workspace)
    if not path.exists():
        return {"version": MANIFEST_VERSION, "entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": MANIFEST_VERSION, "entries": {}}
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        return {"version": MANIFEST_VERSION, "entries": {}}
    return data


def _save_manifest(workspace: Path, manifest: dict[str, Any]) -> None:
    path = _manifest_path(workspace)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except (OSError, TypeError, ValueError):
        pass


def _transcript_path(audio: Path) -> Path:
    return audio.with_name(audio.name + TRANSCRIPT_SUFFIX)


def list_audio(workspace: str | Path) -> list[dict[str, Any]]:
    """List audio meta documents and their processed status."""
    workspace = Path(workspace)
    meta_dir = workspace / "meta"
    manifest = _load_manifest(workspace)
    entries = manifest.get("entries", {})
    out: list[dict[str, Any]] = []
    for fp in _iter_audio_files(meta_dir):
        rel = fp.relative_to(workspace).as_posix()
        entry = entries.get(rel) if isinstance(entries.get(rel), dict) else {}
        transcript = _transcript_path(fp)
        transcribed = bool(
            entry
            and transcript.is_file()
            and entry.get("contentHash") == _safe_hash(fp)
        )
        try:
            size = fp.stat().st_size
        except OSError:
            size = 0
        out.append({
            "path": rel,
            "name": fp.name,
            "size": size,
            "transcribed": transcribed,
            "transcriptPath": transcript.relative_to(workspace).as_posix()
            if transcript.is_file() else None,
            "transcribedAt": entry.get("transcribedAt") if transcribed else None,
            "model": entry.get("model") if transcribed else None,
        })
    return out


def _safe_hash(fp: Path) -> str:
    try:
        return _file_hash(fp)
    except OSError:
        return ""


async def transcribe_pending(
    *,
    workspace: str | Path,
    client: OpenRouterClient,
    model: str,
    language: str | None = None,
    on_event: EventCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> dict[str, Any]:
    """Transcribe audio meta documents that have not been processed before.

    Each STT call runs under the shared timeout+retry guard; a file that
    exhausts its retries is recorded as failed and skipped so the rest still
    process. Returns a summary: ``{transcribed, skipped, failed}``.
    """
    workspace = Path(workspace)
    meta_dir = workspace / "meta"
    on_event = on_event or (lambda _e, _d: None)
    should_cancel = should_cancel or (lambda: False)
    manifest = _load_manifest(workspace)
    entries = manifest.setdefault("entries", {})

    audio_files = _iter_audio_files(meta_dir)
    transcribed: list[str] = []
    skipped: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []

    _log(
        workspace,
        "START",
        f"transcribe_pending — {len(audio_files)} audio file(s) under meta/ "
        f"model={model} language={language or 'auto'}",
    )

    for fp in audio_files:
        if should_cancel():
            raise asyncio.CancelledError()
        rel = fp.relative_to(workspace).as_posix()
        content_hash = _safe_hash(fp)
        transcript = _transcript_path(fp)
        prior = entries.get(rel) if isinstance(entries.get(rel), dict) else {}

        if prior and prior.get("contentHash") == content_hash and transcript.is_file():
            skipped.append({"path": rel, "reason": "already transcribed"})
            _log(workspace, "SKIP", f"{rel} — already transcribed (hash match)")
            continue

        try:
            size = fp.stat().st_size
        except OSError:
            size = 0

        fmt = _audio_format(fp)
        oversized = size > MAX_AUDIO_BYTES
        if oversized and fmt != "wav":
            reason = (
                f"file too large ({size} bytes > {MAX_AUDIO_BYTES}); "
                "convert to WAV to enable automatic chunked transcription"
            )
            failed.append({"path": rel, "reason": reason})
            _log(workspace, "WARN", f"{rel} skipped — {reason}")
            continue

        on_event("transcribe", {"path": rel, "status": "transcribing"})
        try:
            if oversized:
                _log(
                    workspace,
                    "REQUEST",
                    f"{rel} -> STT model={model} fmt=wav bytes={size} "
                    f"(oversized; chunking at {_CHUNK_TARGET_BYTES} bytes)",
                )
                text = await _transcribe_wav_in_chunks(
                    fp,
                    client=client,
                    model=model,
                    language=language,
                    workspace=workspace,
                    rel=rel,
                    on_event=on_event,
                    should_cancel=should_cancel,
                )
            else:
                _log(workspace, "REQUEST", f"{rel} -> STT model={model} fmt={fmt} bytes={size}")
                raw = fp.read_bytes()
                audio_b64 = base64.b64encode(raw).decode("ascii")
                text = await run_guarded(
                    lambda b64=audio_b64: client.transcribe(
                        model=model, audio_b64=b64, fmt=fmt, language=language
                    ),
                    label=f"transcribe {rel}",
                    scope=_SCOPE,
                    workspace=workspace,
                    heartbeat=lambda m: on_event(
                        "transcribe", {"path": rel, "status": "retry", "detail": m}
                    ),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            reason = str(exc) or type(exc).__name__
            failed.append({"path": rel, "reason": reason})
            _log(workspace, "ERROR", f"{rel} transcription failed — skipping ({reason})")
            on_event("transcribe", {"path": rel, "status": "error"})
            continue

        _write_transcript(transcript, source_rel=rel, model=model, text=text)
        entries[rel] = {
            "contentHash": content_hash,
            "transcriptPath": transcript.relative_to(workspace).as_posix(),
            "transcribedAt": _now_iso(),
            "model": model,
        }
        _save_manifest(workspace, manifest)
        transcribed.append(rel)
        _log(
            workspace,
            "WRITE",
            f"{rel} -> {transcript.relative_to(workspace).as_posix()} "
            f"({len(text)} chars)",
        )
        on_event("transcribe", {"path": rel, "status": "done"})

    _log(
        workspace,
        "SUCCESS",
        f"transcribe_pending DONE — transcribed={len(transcribed)} "
        f"skipped={len(skipped)} failed={len(failed)}",
    )
    return {"transcribed": transcribed, "skipped": skipped, "failed": failed}


def _write_transcript(path: Path, *, source_rel: str, model: str, text: str) -> None:
    header = (
        f"# Transcript: {Path(source_rel).name}\n\n"
        f"- Source audio: `{source_rel}`\n"
        f"- Transcribed: {_now_iso()}\n"
        f"- Model: `{model}`\n\n"
        "---\n\n"
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(header + text.strip() + "\n", encoding="utf-8")
    except OSError:
        pass
