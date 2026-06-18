#!/usr/bin/env bash
# Workspace integration smoke (legacy fixture). Mirrors workspace-smoke.ps1.
set -uo pipefail
APP="$(cd "$(dirname "$0")/.." && pwd)"
FAILED=0

step() {
  local n="$1"; shift
  echo ""
  echo "=== $n ==="
  if "$@"; then echo "OK: $n"; else echo "FAIL: $n"; FAILED=$((FAILED+1)); fi
}

step "Rust workspace unit tests" bash -c "cd \"$APP/src-tauri\" && cargo test workspace:: --quiet"
step "Sidecar preview pytest" bash -c "cd \"$APP/sidecar\" && uv run pytest tests/test_preview.py -q"

step "Legacy meta preview via Python" bash -c '
  cd "'"$APP"'/sidecar"
  uv run python - <<PY
import sys, tempfile
from pathlib import Path
sys.path.insert(0, "src")
from contextful_sidecar.runtime.preview import preview_file
tmp = Path(tempfile.mkdtemp())
ws = tmp / "ws"
(ws / "meta").mkdir(parents=True)
(ws / "meta" / "requirements.md").write_text("# req", encoding="utf-8")
(ws / "meta" / "data.csv").write_text("h,v\n1,2", encoding="utf-8")
assert preview_file(ws, "requirements.md", base="meta")["kind"] == "text"
assert preview_file(ws, "data.csv", base="meta")["kind"] == "table"
print("ok")
PY'

echo ""
if [ $FAILED -gt 0 ]; then echo "$FAILED workspace smoke step(s) FAILED"; exit 1; else echo "All workspace smoke steps passed"; fi
