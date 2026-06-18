#!/usr/bin/env python3
"""Build the Contextful sidecar binary with PyInstaller."""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT.parent / "src-tauri" / "binaries"

TARGET_TRIPLES = {
    ("Windows", "AMD64"): "x86_64-pc-windows-msvc",
    ("Darwin", "arm64"): "aarch64-apple-darwin",
    ("Darwin", "x86_64"): "x86_64-apple-darwin",
    ("Linux", "x86_64"): "x86_64-unknown-linux-gnu",
    ("Linux", "aarch64"): "aarch64-unknown-linux-gnu",
}


def target_triple() -> str:
    key = (platform.system(), platform.machine())
    if t := TARGET_TRIPLES.get(key):
        return t
    raise RuntimeError(f"unsupported platform: {key}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dist = ROOT / "dist"
    if dist.exists():
        shutil.rmtree(dist)
    args = [
        sys.executable, "-m", "PyInstaller", "--onefile",
        "--collect-data", "certifi", "--name", "contextful-sidecar",
        "--paths", str(ROOT / "src"),
        str(ROOT / "src" / "contextful_sidecar" / "__main__.py"),
    ]
    # --noconsole/--windowed on macOS produces a .app bundle, not the plain CLI
    # executable Tauri's externalBin expects. Apply it on Windows only.
    if sys.platform == "win32":
        args.insert(4, "--noconsole")
    subprocess.run(args, cwd=ROOT, check=True)
    built = dist / ("contextful-sidecar.exe" if sys.platform == "win32" else "contextful-sidecar")
    suffix = ".exe" if sys.platform == "win32" else ""
    dest = OUT_DIR / f"contextful-sidecar-{target_triple()}{suffix}"
    shutil.copy2(built, dest)
    print(f"Built sidecar -> {dest}")


if __name__ == "__main__":
    main()
