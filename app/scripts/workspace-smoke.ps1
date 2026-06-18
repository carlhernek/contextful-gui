# Workspace integration smoke (legacy fixture). Mirrors workspace-smoke.sh.
$ErrorActionPreference = "Continue"
$App = (Resolve-Path "$PSScriptRoot\..").Path
$Failed = 0

function Step {
    param([string]$Name, [scriptblock]$Action)
    Write-Host ""
    Write-Host "=== $Name ==="
    try {
        $ok = & $Action
        if ($ok) { Write-Host "OK: $Name" } else { Write-Host "FAIL: $Name"; $script:Failed++ }
    } catch {
        Write-Host "FAIL: $Name ($_)"
        $script:Failed++
    }
}

Step "Rust workspace unit tests" {
    Push-Location "$App\src-tauri"
    cargo test --quiet 2>&1 | Out-Host
    $code = $LASTEXITCODE
    Pop-Location
    return ($code -eq 0)
}

Step "Sidecar indexing pytest" {
    Push-Location "$App\sidecar"
    uv run pytest tests/test_indexing.py -q 2>&1 | Out-Host
    $code = $LASTEXITCODE
    Pop-Location
    return ($code -eq 0)
}

Step "Index refresh fixture" {
    Push-Location "$App\sidecar"
    $code = @"
import asyncio, json, sys, tempfile
from pathlib import Path
sys.path.insert(0, 'src')
from contextful_sidecar.runtime.indexing import INDEX_FILE, refresh_index, scan_items

class FakeClient:
    async def chat_completion(self, **kwargs):
        return {'choices': [{'message': {'content': '{"description":"fixture repo","keywords":["fixture"]}'}}]}

async def main():
    tmp = Path(tempfile.mkdtemp())
    ws = tmp / 'ws'
    ws.mkdir()
    (ws / '.contextful.json').write_text(json.dumps({
        'display_name': 'x', 'project_type': 'both',
        'repos': [{'name': 'backoffice', 'url': 'u', 'branch': 'main'}]
    }), encoding='utf-8')
    (ws / 'repos' / 'backoffice').mkdir(parents=True)
    (ws / 'repos' / 'backoffice' / 'README.md').write_text('# Backoffice', encoding='utf-8')
    items = scan_items(ws)
    assert any(i['id'] == 'repo:backoffice' for i in items)
    await refresh_index(workspace=ws, client=FakeClient(), models={'module': 'test'}, skip_enrichment=True)
    data = json.loads((ws / INDEX_FILE).read_text(encoding='utf-8'))
    assert any(i['id'] == 'repo:backoffice' for i in data['items'])
    print('ok')

asyncio.run(main())
"@ | uv run python -
    Pop-Location
    return ($LASTEXITCODE -eq 0)
}

Step "Sidecar preview pytest" {
    Push-Location "$App\sidecar"
    uv run pytest tests/test_preview.py -q 2>&1 | Out-Host
    $code = $LASTEXITCODE
    Pop-Location
    return ($code -eq 0)
}

Step "Legacy meta preview via Python" {
    Push-Location "$App\sidecar"
    $code = @"
import sys, tempfile
from pathlib import Path
sys.path.insert(0, 'src')
from contextful_sidecar.runtime.preview import preview_file
tmp = Path(tempfile.mkdtemp())
(ws := tmp / 'ws').mkdir()
(ws / 'meta').mkdir()
(ws / 'meta' / 'requirements.md').write_text('# req', encoding='utf-8')
(ws / 'meta' / 'data.csv').write_text('h,v\n1,2', encoding='utf-8')
r1 = preview_file(ws, 'requirements.md', base='meta')
r2 = preview_file(ws, 'data.csv', base='meta')
assert r1['ok'] and r1['kind'] == 'text'
assert r2['ok'] and r2['kind'] == 'table'
print('ok')
"@ | uv run python -
    Pop-Location
    return ($LASTEXITCODE -eq 0)
}

Write-Host ""
if ($Failed -gt 0) {
    Write-Host "$Failed workspace smoke step(s) FAILED"
    exit 1
} else {
    Write-Host "All workspace smoke steps passed"
    exit 0
}
