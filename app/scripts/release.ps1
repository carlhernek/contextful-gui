# Publish a GitHub release after release-gate passes. Usage: .\release.ps1 [-Version 1.2.1]
param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$App = (Resolve-Path "$PSScriptRoot\..").Path

if (-not $Version) {
    $Version = (Get-Content "$App\src-tauri\tauri.conf.json" -Raw | ConvertFrom-Json).version
}

Write-Host "Running release gate..."
& "$PSScriptRoot\release-gate.ps1"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Release gate failed (exit $LASTEXITCODE) — aborting publish"
    exit $LASTEXITCODE
}

$Nsis = "$App\src-tauri\target\release\bundle\nsis\Contextful_${Version}_x64-setup.exe"
$Msi = "$App\src-tauri\target\release\bundle\msi\Contextful_${Version}_x64_en-US.msi"

if (-not (Test-Path $Nsis)) { throw "Missing installer: $Nsis" }
if (-not (Test-Path $Msi)) { throw "Missing installer: $Msi" }

Write-Host "Creating GitHub release v$Version..."
gh release create "v$Version" `
    --title "Contextful $Version" `
    --notes "Release built via release-gate (frozen sidecar RPC-verified)." `
    "$Nsis#Contextful_${Version}_x64-setup.exe" `
    "$Msi#Contextful_${Version}_x64_en-US.msi"

Write-Host "Published v$Version"
