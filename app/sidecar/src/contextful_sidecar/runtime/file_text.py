"""Shared on-disk text extraction for indexing and tools."""
from __future__ import annotations

import json
import re
from pathlib import Path

BINARY_EXTENSIONS = {
    ".docx", ".doc", ".pdf", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".xlsx", ".xls", ".pptx", ".ppt", ".bin", ".exe", ".dll",
}
DOCX_EXTENSIONS = {".docx", ".doc"}
SNIPPET_CAP = 500


def is_binary_path(path: Path) -> bool:
    return path.suffix.lower() in BINARY_EXTENSIONS


def extract_docx_text(path: Path, cap: int = SNIPPET_CAP) -> str:
    from docx import Document

    doc = Document(path)
    lines = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    text = "\n".join(lines) if lines else ""
    if len(text) > cap:
        return text[:cap] + "..."
    return text


def read_text_snippet(path: Path, cap: int = SNIPPET_CAP) -> str:
    """Best-effort text preview for scan snippets and binary meta files."""
    if not path.is_file():
        return ""
    suffix = path.suffix.lower()
    if suffix in DOCX_EXTENSIONS:
        try:
            return extract_docx_text(path, cap=cap)
        except Exception:
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            return f"binary file ({size} bytes)"
    if is_binary_path(path):
        try:
            size = path.stat().st_size
        except OSError:
            return ""
        return f"binary file ({size} bytes)"
    try:
        data = path.read_bytes()[: cap * 4]
        text = data.decode("utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) > cap:
        return text[:cap] + "..."
    return text


def read_file_as_text(path: Path, cap: int = 500_000) -> str:
    """Read file as text for read_file tool; extracts docx, rejects other binaries."""
    if path.suffix.lower() in DOCX_EXTENSIONS:
        try:
            text = extract_docx_text(path, cap=cap)
            return text or "(empty document)"
        except Exception as exc:
            return f"ERROR: could not read docx: {exc}"
    if is_binary_path(path):
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        return f"binary file ({size} bytes) — cannot read as text"
    data = path.read_text(encoding="utf-8", errors="replace")
    if len(data) > cap:
        data = data[:cap] + "\n...[truncated]"
    return data


def _first_readme_excerpt(repo_dir: Path, cap: int) -> str:
    for name in ("README.md", "README", "README.txt"):
        fp = repo_dir / name
        if fp.is_file():
            try:
                text = fp.read_text(encoding="utf-8", errors="replace")[:cap]
            except OSError:
                continue
            for line in text.splitlines():
                line = line.strip()
                if line:
                    if line.startswith("#"):
                        return re.sub(r"^#+\s*", "", line)[:120]
                    return line[:120]
            return text.strip()[:120]
    return ""


def _manifest_excerpt(repo_dir: Path, cap: int) -> str:
    pubspec = repo_dir / "pubspec.yaml"
    if pubspec.is_file():
        try:
            text = pubspec.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        m = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
        if m:
            return m.group(1).strip().strip('"').strip("'")[:120]
        m = re.search(r"^name:\s*(.+)$", text, re.MULTILINE)
        if m:
            return f"Flutter project {m.group(1).strip()}"[:120]
    pkg = repo_dir / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ""
        desc = str(data.get("description") or "").strip()
        name = str(data.get("name") or "").strip()
        if desc:
            return desc[:120]
        if name:
            return f"Node project {name}"[:120]
    cargo = repo_dir / "Cargo.toml"
    if cargo.is_file():
        try:
            text = cargo.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        m = re.search(r"description\s*=\s*\"([^\"]+)\"", text)
        if m:
            return m.group(1)[:120]
    return ""


def repo_scan_snippet(repo_dir: Path, name: str, cap: int = SNIPPET_CAP) -> str:
    if not repo_dir.is_dir():
        return f"Repository {name} (not cloned)"
    excerpt = _first_readme_excerpt(repo_dir, cap) or _manifest_excerpt(repo_dir, cap)
    if excerpt:
        return excerpt
    return f"Cloned repository {name}"
