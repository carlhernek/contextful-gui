#!/usr/bin/env python3
"""Post-build smoke: exercise the frozen PyInstaller sidecar binary via NDJSON."""
from __future__ import annotations

import json
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BINARIES = ROOT / "src-tauri" / "binaries"

TARGET_TRIPLES = {
    ("Windows", "AMD64"): "contextful-sidecar-x86_64-pc-windows-msvc.exe",
    ("Darwin", "arm64"): "contextful-sidecar-aarch64-apple-darwin",
    ("Darwin", "x86_64"): "contextful-sidecar-x86_64-apple-darwin",
    ("Linux", "x86_64"): "contextful-sidecar-x86_64-unknown-linux-gnu",
    ("Linux", "aarch64"): "contextful-sidecar-aarch64-unknown-linux-gnu",
}


def sidecar_binary() -> Path:
    key = (platform.system(), platform.machine())
    name = TARGET_TRIPLES.get(key)
    if not name:
        raise RuntimeError(f"unsupported platform for release-smoke: {key}")
    path = BINARIES / name
    if not path.exists():
        raise FileNotFoundError(f"frozen sidecar not found: {path} (run npm run build:sidecar first)")
    return path


def _seed_workspace(root: Path) -> Path:
    ws = root / "ws"
    ws.mkdir()
    (ws / ".contextful.json").write_text(
        json.dumps({
            "display_name": "release-smoke",
            "project_type": "both",
            "repos": [{"name": "web", "url": "u", "branch": "main"}],
        }),
        encoding="utf-8",
    )
    (ws / "repos" / "web").mkdir(parents=True)
    (ws / "repos" / "web" / "README.md").write_text("# Web app\n", encoding="utf-8")
    (ws / "meta").mkdir()
    (ws / "modules" / "security-analysis").mkdir(parents=True)
    return ws


def rpc(proc: subprocess.Popen, req_id: str, method: str, params: dict | None = None) -> dict:
    line = json.dumps({"id": req_id, "method": method, "params": params or {}})
    assert proc.stdin is not None
    assert proc.stdout is not None
    proc.stdin.write(line + "\n")
    proc.stdin.flush()
    while True:
        raw = proc.stdout.readline()
        if not raw:
            raise RuntimeError(f"sidecar exited before response to {method}")
        data = json.loads(raw)
        if data.get("id") != req_id:
            continue
        if "event" in data:
            continue
        return data


def assert_not_unknown(resp: dict, method: str) -> None:
    err = resp.get("error", "")
    if "unknown method" in str(err).lower():
        raise AssertionError(f"{method} returned unknown method — stale frozen sidecar? {resp}")
    if "error" in resp and method in ("configure", "refresh_index", "preview"):
        raise AssertionError(f"{method} failed: {resp}")


def main() -> int:
    binary = sidecar_binary()
    print(f"release-smoke: using {binary}")

    with tempfile.TemporaryDirectory() as tmp:
        ws = _seed_workspace(Path(tmp))
        ws_str = str(ws)

        kwargs: dict = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
            "text": True,
            "bufsize": 1,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

        proc = subprocess.Popen([str(binary)], **kwargs)
        try:
            cfg = rpc(proc, "cfg", "configure", {"api_key": "fake-key"})
            assert_not_unknown(cfg, "configure")
            assert cfg.get("result", {}).get("ok") is True

            refresh = rpc(proc, "ref", "refresh_index", {
                "workspace": ws_str,
                "skipEnrichment": True,
            })
            assert_not_unknown(refresh, "refresh_index")
            assert refresh.get("result", {}).get("ok") is True
            index_path = ws / ".workspace-index.json"
            if not index_path.exists():
                raise AssertionError("refresh_index did not create .workspace-index.json")

            enrich = rpc(proc, "enr", "enrich_index_item", {
                "workspace": ws_str,
                "itemId": "repo:web",
            })
            assert_not_unknown(enrich, "enrich_index_item")

            preview = rpc(proc, "prv", "preview", {
                "workspace": ws_str,
                "path": "README.md",
                "base": "repos/web",
            })
            assert_not_unknown(preview, "preview")
            assert preview.get("result", {}).get("ok") is True

            print("OK: frozen sidecar RPC matrix passed")
            return 0
        finally:
            if proc.stdin:
                proc.stdin.close()
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
