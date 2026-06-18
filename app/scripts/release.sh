#!/usr/bin/env bash
# Publish a GitHub release after release-gate passes.
set -euo pipefail
APP="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${1:-$(python3 -c "import json; print(json.load(open('$APP/src-tauri/tauri.conf.json'))['version'])")}"

echo "Running release gate..."
bash "$APP/scripts/release-gate.sh"

NSIS="$APP/src-tauri/target/release/bundle/nsis/Contextful_${VERSION}_x64-setup.exe"
MSI="$APP/src-tauri/target/release/bundle/msi/Contextful_${VERSION}_x64_en-US.msi"

# macOS/linux bundle paths differ — adjust if needed
if [[ "$(uname -s)" == "Darwin" ]]; then
  echo "Note: adjust release.sh for macOS bundle paths before publishing on macOS"
fi

test -f "$NSIS" || { echo "Missing $NSIS"; exit 1; }
test -f "$MSI" || { echo "Missing $MSI"; exit 1; }

gh release create "v$VERSION" \
  --title "Contextful $VERSION" \
  --notes "Release built via release-gate (frozen sidecar RPC-verified)." \
  "$NSIS#Contextful_${VERSION}_x64-setup.exe" \
  "$MSI#Contextful_${VERSION}_x64_en-US.msi"

echo "Published v$VERSION"
