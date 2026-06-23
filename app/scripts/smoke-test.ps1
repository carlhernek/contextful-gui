# Contextful smoke gate (Windows). Mirrors scripts/smoke-test.sh.
$ErrorActionPreference = "Continue"
$App = (Resolve-Path "$PSScriptRoot\..").Path
$script:Failed = 0

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

Step "Frontend build" {
    Push-Location $App
    npm run build | Out-Host
    $code = $LASTEXITCODE
    Pop-Location
    return ($code -eq 0)
}

Step "Frontend unit tests" {
    Push-Location $App
    npm run test 2>&1 | Out-Host
    $code = $LASTEXITCODE
    Pop-Location
    return ($code -eq 0)
}

Step "Rust compile" {
    Push-Location "$App\src-tauri"
    cargo check --quiet | Out-Host
    $code = $LASTEXITCODE
    Pop-Location
    return ($code -eq 0)
}

Step "Sidecar smoke" {
    Push-Location "$App\sidecar"
    uv run python tests/smoke.py | Out-Host
    $code = $LASTEXITCODE
    Pop-Location
    return ($code -eq 0)
}

Step "Rust workspace tests" {
    Push-Location "$App\src-tauri"
    cargo test --quiet 2>&1 | Out-Host
    $code = $LASTEXITCODE
    Pop-Location
    return ($code -eq 0)
}

Step "Sidecar pytest" {
    Push-Location "$App\sidecar"
    uv run pytest tests/ -q 2>&1 | Out-Host
    $code = $LASTEXITCODE
    Pop-Location
    return ($code -eq 0)
}

Step "Workspace integration" {
    & "$PSScriptRoot\workspace-smoke.ps1"
    return ($LASTEXITCODE -eq 0)
}

Step "NDJSON round-trip" {
    Push-Location "$App\sidecar"
    $out = '{"id":"a","method":"configure","params":{"api_key":"fake"}}' | uv run python -m contextful_sidecar
    Pop-Location
    return ($out -match '"ok":\s*true')
}

Step "Worktree flow" {
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("cf-smoke-" + [System.Guid]::NewGuid().ToString("N"))
    try {
        $fixture = Join-Path $tmp "modules-fixture"
        $skillDir = Join-Path $fixture "modules\security-analysis"
        New-Item -ItemType Directory -Force -Path $skillDir | Out-Null
        Set-Content -Path (Join-Path $fixture "modules\template-version.txt") -Value "1.0.0"
        Set-Content -Path (Join-Path $skillDir "SKILL.md") -Value "# Security Analysis (fixture)"
        git -C $fixture init -q -b main
        git -C $fixture -c user.email=smoke@contextful -c user.name=smoke add -A
        git -C $fixture -c user.email=smoke@contextful -c user.name=smoke commit -q -m "fixture"
        $template = Join-Path $tmp "template"
        git clone -q $fixture $template
        $project = Join-Path $tmp "projects\smoke"
        git -C $template worktree add -q $project -b "project/smoke-test"
        $hasSkill = Test-Path (Join-Path $project "modules\security-analysis\SKILL.md")
        $branches = git -C $template branch --list "project/smoke-test"
        return ($hasSkill -and ($branches -match "project/smoke-test"))
    } finally {
        if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue }
    }
}

Write-Host ""
if ($script:Failed -gt 0) {
    Write-Host "$($script:Failed) smoke step(s) FAILED"
    exit 1
} else {
    Write-Host "All smoke steps passed"
    exit 0
}
