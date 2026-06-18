#!/usr/bin/env python3
"""Assert the frozen sidecar binary is newer than sidecar source."""
from __future__ import annotations

import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BINARIES = ROOT / "src-tauri" / "binaries"
SRC = ROOT / "sidecar" / "src"

TARGET_TRIPLES = {
    ("Windows", "AMD64"): "contextful-sidecar-x86_64-pc-windows-msvc.exe",
    ("Darwin", "arm64"): "contextful-sidecar-aarch64-apple-darwin",
    ("Darwin", "x86_64"): "contextful-sidecar-x86_64-apple-darwin",
    ("Linux", "x86_64"): "contextful-sidecar-x86_64-unknown-linux-gnu",
    ("Linux", "aarch64"): "contextful-sidecar-aarch64-unknown-linux-gnu",
}


def main() -> int:
    key = (platform.system(), platform.machine())
    name = TARGET_TRIPLES.get(key)
    if not name:
        print(f"FAIL: unsupported platform {key}")
        return 1
    binary = BINARIES / name
    if not binary.exists():
        print(f"FAIL: missing {binary}")
        return 1

    newest_src = max(p.stat().st_mtime for p in SRC.rglob("*.py"))
    bin_mtime = binary.stat().st_mtime
    if bin_mtime < newest_src:
        print(f"FAIL: sidecar binary older than source ({bin_mtime} < {newest_src})")
        print("Run: npm run build:sidecar")
        return 1
    print(f"OK: {binary.name} is newer than sidecar source")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
