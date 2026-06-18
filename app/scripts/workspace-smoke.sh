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

step "Rust workspace unit tests" bash -c "cd \"$APP/src-tauri\" && cargo test --quiet"
step "Sidecar indexing pytest" bash -c "cd \"$APP/sidecar\" && uv run pytest tests/test_indexing.py -q"

step "Index refresh fixture" bash -c '
  cd "'"$APP"'/sidecar"
  uv run python - <<PY
import asyncio, json, sys, tempfile
from pathlib import Path
sys.path.insert(0, "src")
from contextful_sidecar.runtime.indexing import INDEX_FILE, refresh_index, scan_items

class FakeClient:
    async def chat_completion(self, **kwargs):
        return {"choices": [{"message": {"content": "{\"description\":\"fixture repo\",\"keywords\":[\"fixture\"]}"}}]}

async def main():
    tmp = Path(tempfile.mkdtemp())
    ws = tmp / "ws"
    ws.mkdir()
    (ws / ".contextful.json").write_text(json.dumps({
        "display_name": "x", "project_type": "both",
        "repos": [{"name": "backoffice", "url": "u", "branch": "main"}]
    }), encoding="utf-8")
    (ws / "repos" / "backoffice").mkdir(parents=True)
    (ws / "repos" / "backoffice" / "README.md").write_text("# Backoffice", encoding="utf-8")
    assert any(i["id"] == "repo:backoffice" for i in scan_items(ws))
    await refresh_index(workspace=ws, client=FakeClient(), models={"module": "test"}, skip_enrichment=True)
    data = json.loads((ws / INDEX_FILE).read_text(encoding="utf-8"))
    assert any(i["id"] == "repo:backoffice" for i in data["items"])
    print("ok")

asyncio.run(main())
PY'

step "Sidecar preview pytest" bash -c "cd \"$APP/sidecar\" && uv run pytest tests/test_preview.py -q"

step "Workspace index module pytest" bash -c "cd \"$APP/sidecar\" && uv run pytest tests/test_workspace_index.py -q"

step "Legacy project index pytest (v1.0.0, v1.1.0)" bash -c "cd \"$APP/sidecar\" && uv run pytest tests/test_legacy_index.py -q"

step "Legacy project index smoke (v1.0.0, v1.1.0)" bash -c "cd \"$APP/sidecar\" && uv run python ../scripts/legacy-project-smoke.py"

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
