<#
.SYNOPSIS
Run the full hot-demo pipeline end-to-end.

.DESCRIPTION
Orchestrates the complete demo flow: CSV export, blob upload with Event Hub
trigger, enrichment wait, and search validation.

Requires environment variables from scripts/demo/.env (loaded automatically).

.PARAMETER DryRun
Run all steps in dry-run mode (no side effects).

.EXAMPLE
.\scripts\demo\run_full_demo.ps1
.\scripts\demo\run_full_demo.ps1 -DryRun
#>

param(
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$repoRoot = Resolve-Path "$PSScriptRoot\..\.."
$startTime = Get-Date

# ---------------------------------------------------------------------------
# Load environment variables from .env file
# ---------------------------------------------------------------------------

$envFile = Join-Path $PSScriptRoot ".env"
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        $trimmed = $line.Trim()
        if ($trimmed -and -not $trimmed.StartsWith('#')) {
            if ($trimmed -match '^([^=]+)=(.*)$') {
                $key = $Matches[1].Trim()
                $value = $Matches[2].Trim().Trim('"').Trim("'")
                [System.Environment]::SetEnvironmentVariable($key, $value, 'Process')
            }
        }
    }
    Write-Host "  Loaded env from: $envFile" -ForegroundColor DarkGray
} else {
    Write-Host "  WARNING: No .env file found at $envFile" -ForegroundColor Yellow
    Write-Host "           Copy scripts/demo/.env.example to scripts/demo/.env and fill in values." -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Write-Step {
    param([int]$Number, [string]$Message)
    Write-Host ""
    Write-Host "====================================================" -ForegroundColor Cyan
    Write-Host "  Step ${Number}: $Message" -ForegroundColor Cyan
    Write-Host "====================================================" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "  [OK] $Message" -ForegroundColor Green
}

function Write-Skip {
    param([string]$Message)
    Write-Host "  [SKIP] $Message" -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "  ================================================================" -ForegroundColor White
Write-Host "    Holiday Peak Hub — Full Demo Pipeline" -ForegroundColor White
Write-Host "    Mode: $(if ($DryRun) { 'DRY-RUN' } else { 'LIVE' })" -ForegroundColor $(if ($DryRun) { 'Magenta' } else { 'Green' })
Write-Host "  ================================================================" -ForegroundColor White
Write-Host ""

$stepResults = @()

# ---------------------------------------------------------------------------
# Step 1: Export product CSV
# ---------------------------------------------------------------------------

Write-Step -Number 1 -Message "Export product CSV (mock data)"

$exportScript = Join-Path $repoRoot "scripts\demo\export_products_csv.py"
python $exportScript --mock
if ($LASTEXITCODE -ne 0) { throw "CSV export failed." }
Write-Ok "Product CSV exported"
$stepResults += [PSCustomObject]@{ Step = 1; Name = "Export CSV"; Status = "DONE" }

# ---------------------------------------------------------------------------
# Step 2: Upload to Blob + trigger enrichment
# ---------------------------------------------------------------------------

Write-Step -Number 2 -Message "Upload to Blob Storage and trigger enrichment"

$csvPath = Join-Path $repoRoot "docs\demos\sample-data\products_export.csv"
$uploadScript = Join-Path $repoRoot "scripts\demo\upload_and_trigger.py"

$uploadArgs = @($uploadScript, "--csv-path", $csvPath)
if ($DryRun) { $uploadArgs += "--dry-run" }

python @uploadArgs
if ($LASTEXITCODE -ne 0) { throw "Upload and trigger failed." }
Write-Ok "Upload and trigger complete"
$stepResults += [PSCustomObject]@{ Step = 2; Name = "Upload + Trigger"; Status = "DONE" }

# ---------------------------------------------------------------------------
# Step 3: Wait for enrichment pipeline
# ---------------------------------------------------------------------------

Write-Step -Number 3 -Message "Wait for enrichment pipeline processing"

if ($DryRun) {
    Write-Skip "Pipeline wait (dry-run mode)"
    $stepResults += [PSCustomObject]@{ Step = 3; Name = "Enrichment Wait"; Status = "SKIPPED" }
} else {
    $waitSeconds = 30
    Write-Host "  Waiting ${waitSeconds}s for enrichment pipeline to process..." -ForegroundColor Gray
    for ($i = $waitSeconds; $i -gt 0; $i -= 5) {
        Write-Host "    ${i}s remaining..." -ForegroundColor DarkGray
        Start-Sleep -Seconds ([Math]::Min(5, $i))
    }
    Write-Ok "Enrichment wait complete"
    $stepResults += [PSCustomObject]@{ Step = 3; Name = "Enrichment Wait"; Status = "DONE" }
}

# ---------------------------------------------------------------------------
# Step 4: Validate search results
# ---------------------------------------------------------------------------

Write-Step -Number 4 -Message "Validate catalog search"

$validateScript = Join-Path $repoRoot "scripts\demo\validate_search.py"

$validateArgs = @($validateScript)
if ($DryRun) { $validateArgs += "--dry-run" }
if ($env:CATALOG_SEARCH_URL) { $validateArgs += "--catalog-url"; $validateArgs += $env:CATALOG_SEARCH_URL }

python @validateArgs
if ($LASTEXITCODE -ne 0) { throw "Search validation failed." }
Write-Ok "Search validation passed"
$stepResults += [PSCustomObject]@{ Step = 4; Name = "Validate Search"; Status = "DONE" }

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

$elapsed = (Get-Date) - $startTime

Write-Host ""
Write-Host "====================================================" -ForegroundColor Cyan
Write-Host "  Demo Pipeline — Summary" -ForegroundColor Cyan
Write-Host "====================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host ("  {0,-6} {1,-25} {2}" -f "STEP", "NAME", "STATUS") -ForegroundColor White
Write-Host ("  {0,-6} {1,-25} {2}" -f "----", "----", "------") -ForegroundColor DarkGray

foreach ($entry in $stepResults) {
    $color = switch ($entry.Status) {
        "DONE"    { "Green" }
        "SKIPPED" { "Yellow" }
        default   { "Red" }
    }
    Write-Host ("  {0,-6} {1,-25} {2}" -f $entry.Step, $entry.Name, $entry.Status) -ForegroundColor $color
}

Write-Host ""
Write-Host "  Total elapsed: $($elapsed.ToString('mm\:ss'))" -ForegroundColor Gray
Write-Host "  Mode: $(if ($DryRun) { 'DRY-RUN' } else { 'LIVE' })" -ForegroundColor $(if ($DryRun) { 'Magenta' } else { 'Green' })
Write-Host ""
Write-Host "  Demo pipeline complete." -ForegroundColor Green
Write-Host ""
