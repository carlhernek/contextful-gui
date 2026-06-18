# Contextful release gate (Windows). Phase A smoke → Phase B build → Phase C frozen sidecar smoke.
$ErrorActionPreference = "Stop"
$App = (Resolve-Path "$PSScriptRoot\..").Path
$Failed = 0

function Step {
    param([string]$Name, [scriptblock]$Action)
    Write-Host ""
    Write-Host "=== $Name ==="
    try {
        & $Action
        if ($LASTEXITCODE -ne 0) { throw "exit $LASTEXITCODE" }
        Write-Host "OK: $Name"
    } catch {
        Write-Host "FAIL: $Name ($_)"
        $script:Failed++
    }
}

Write-Host "Contextful release gate (Phase A-C)"
$script:Failed = 0

function Step {
    param([string]$Name, [scriptblock]$Action)
    Write-Host ""
    Write-Host "=== $Name ==="
    try {
        & $Action
        if ($LASTEXITCODE -ne 0) { throw "exit $LASTEXITCODE" }
        Write-Host "OK: $Name"
    } catch {
        Write-Host "FAIL: $Name ($_)"
        $script:Failed++
        throw
    }
}

try {
    Step "Phase A: pre-build smoke" {
        & "$PSScriptRoot\smoke-test.ps1"
    }

    Step "Phase B: full build (sidecar + tauri)" {
        Push-Location $App
        npm run build:all
        Pop-Location
    }

    Step "Phase B: sidecar binary freshness" {
        Push-Location "$App\sidecar"
        uv run python ..\scripts\check-sidecar-freshness.py
        Pop-Location
    }

    Step "Phase C: frozen sidecar RPC smoke" {
        Push-Location "$App\sidecar"
        uv run python ..\scripts\release-smoke.py
        Pop-Location
    }
} catch {
    # Step already logged failure
}

Write-Host ""
if ($script:Failed -gt 0) {
    Write-Host "$Failed release gate step(s) FAILED — do not publish"
    exit 1
} else {
    Write-Host "Release gate PASSED — safe to run scripts/release.ps1"
    exit 0
}
