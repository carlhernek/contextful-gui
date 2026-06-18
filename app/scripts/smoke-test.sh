#!/usr/bin/env bash
# Contextful smoke gate (macOS/Linux). Mirrors scripts/smoke-test.ps1.
set -uo pipefail
APP="$(cd "$(dirname "$0")/.." && pwd)"
FAILED=0

step() {
  local n="$1"; shift
  echo ""
  echo "=== $n ==="
  if "$@"; then echo "OK: $n"; else echo "FAIL: $n"; FAILED=$((FAILED+1)); fi
}

step "Frontend build"    bash -c "cd \"$APP\" && npm run build"
step "Rust compile"      bash -c "cd \"$APP/src-tauri\" && cargo check --quiet"
step "Sidecar smoke"     bash -c "cd \"$APP/sidecar\" && uv run python tests/smoke.py"
step "Rust workspace tests" bash -c "cd \"$APP/src-tauri\" && cargo test workspace:: --quiet"
step "Sidecar pytest"   bash -c "cd \"$APP/sidecar\" && uv run pytest tests/ -q"
step "Workspace integration" bash -c "\"$APP/scripts/workspace-smoke.sh\""

step "NDJSON round-trip" bash -c '
  cd "'"$APP"'/sidecar"
  out=$(printf "%s\n" "{\"id\":\"a\",\"method\":\"configure\",\"params\":{\"api_key\":\"fake\"}}" | uv run python -m contextful_sidecar)
  echo "$out" | grep -q "\"ok\": true"'

step "Worktree flow"     bash -c '
  tmp=$(mktemp -d); trap "rm -rf \"$tmp\"" EXIT
  fixture="$tmp/modules-fixture"
  mkdir -p "$fixture/modules/security-analysis"
  echo "1.0.0" > "$fixture/modules/template-version.txt"
  echo "# Security Analysis (fixture)" > "$fixture/modules/security-analysis/SKILL.md"
  git -C "$fixture" init -q -b main
  git -C "$fixture" -c user.email=smoke@contextful -c user.name=smoke add -A
  git -C "$fixture" -c user.email=smoke@contextful -c user.name=smoke commit -q -m "fixture"
  git clone -q "$fixture" "$tmp/template"
  git -C "$tmp/template" worktree add -q "$tmp/projects/smoke" -b "project/smoke-test"
  test -f "$tmp/projects/smoke/modules/security-analysis/SKILL.md" \
    && git -C "$tmp/template" branch --list "project/smoke-test" | grep -q "project/smoke-test"'

echo ""
if [ $FAILED -gt 0 ]; then echo "$FAILED smoke step(s) FAILED"; exit 1; else echo "All smoke steps passed"; fi
