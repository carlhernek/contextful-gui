#!/usr/bin/env bash
# Contextful release gate. Phase A smoke → Phase B build → Phase C frozen sidecar smoke.
set -euo pipefail
APP="$(cd "$(dirname "$0")/.." && pwd)"
FAILED=0

step() {
  local n="$1"; shift
  echo ""
  echo "=== $n ==="
  if "$@"; then echo "OK: $n"; else echo "FAIL: $n"; FAILED=$((FAILED+1)); fi
}

echo "Contextful release gate (Phase A-C)"

step "Phase A: pre-build smoke" bash "$APP/scripts/smoke-test.sh"

step "Phase B: full build (sidecar + tauri)" bash -c "cd \"$APP\" && npm run build:all"

step "Phase B: sidecar binary freshness" bash -c "cd \"$APP/sidecar\" && uv run python ../scripts/check-sidecar-freshness.py"

step "Phase C: frozen sidecar RPC smoke" bash -c "cd \"$APP/sidecar\" && uv run python ../scripts/release-smoke.py"

echo ""
if [ $FAILED -gt 0 ]; then echo "$FAILED release gate step(s) FAILED — do not publish"; exit 1
else echo "Release gate PASSED — safe to run scripts/release.sh"; fi
