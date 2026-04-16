<#
.SYNOPSIS
Build, push, and deploy demo services to AKS.

.DESCRIPTION
Builds Docker images, pushes them to ACR, renders Helm charts, and applies
the manifests to the AKS cluster for the specified services. Designed for
rapid customer demo deployments.

.PARAMETER AcrName
Azure Container Registry name (without .azurecr.io suffix).

.PARAMETER ResourceGroup
Azure resource group containing the AKS cluster.

.PARAMETER AksCluster
AKS cluster name. Auto-detected from the resource group if empty.

.PARAMETER Namespace
Kubernetes namespace for deployment.

.PARAMETER Services
Array of service names to build and deploy.

.PARAMETER DryRun
Print commands without executing them.

.PARAMETER SkipBuild
Skip Docker build step (use existing images).

.PARAMETER SkipPush
Skip Docker push step (image already in ACR).

.EXAMPLE
.\scripts\demo\deploy_demo_services.ps1
.\scripts\demo\deploy_demo_services.ps1 -DryRun
.\scripts\demo\deploy_demo_services.ps1 -Services @("truth-enrichment") -SkipBuild
#>

param(
    [string]$AcrName = "holidaypeakhub405devacr",
    [string]$ResourceGroup = "holidaypeakhub405-dev-rg",
    [string]$AksCluster = "",
    [string]$Namespace = "holiday-peak-agents",
    [string[]]$Services = @("truth-enrichment", "search-enrichment-agent"),
    [switch]$DryRun,
    [switch]$SkipBuild,
    [switch]$SkipPush
)

$ErrorActionPreference = 'Stop'

$repoRoot = Resolve-Path "$PSScriptRoot\..\.."
$acrFqdn = "$AcrName.azurecr.io"
$timestamp = Get-Date -Format 'yyyyMMdd-HHmm'
$renderHelmScript = Join-Path $repoRoot ".infra\azd\hooks\render-helm.ps1"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "=== $Message ===" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "  [OK] $Message" -ForegroundColor Green
}

function Write-Skip {
    param([string]$Message)
    Write-Host "  [SKIP] $Message" -ForegroundColor Yellow
}

function Write-Dry {
    param([string]$Message)
    Write-Host "  [DRY-RUN] $Message" -ForegroundColor Magenta
}

function Invoke-StepCommand {
    param([string]$Description, [scriptblock]$Command)
    if ($DryRun) {
        Write-Dry $Description
    } else {
        Write-Host "  $Description" -ForegroundColor Gray
        & $Command
        if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
            throw "Command failed with exit code $LASTEXITCODE : $Description"
        }
    }
}

# ---------------------------------------------------------------------------
# 1. Validate prerequisites
# ---------------------------------------------------------------------------

Write-Step "Validating prerequisites"

$requiredTools = @("az", "docker", "helm", "kubectl")
foreach ($tool in $requiredTools) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "Required tool '$tool' is not available on PATH."
    }
    Write-Ok "$tool found"
}

# ---------------------------------------------------------------------------
# 2. ACR login
# ---------------------------------------------------------------------------

Write-Step "Logging into ACR ($AcrName)"

Invoke-StepCommand "az acr login --name $AcrName" {
    az acr login --name $AcrName -ErrorAction Stop
}

# ---------------------------------------------------------------------------
# 3. AKS credentials
# ---------------------------------------------------------------------------

Write-Step "Getting AKS credentials"

if (-not $AksCluster) {
    Write-Host "  Auto-detecting AKS cluster in resource group '$ResourceGroup'..." -ForegroundColor Gray
    if ($DryRun) {
        Write-Dry "az aks list --resource-group $ResourceGroup --query [0].name -o tsv"
        $AksCluster = "auto-detected-cluster"
    } else {
        $AksCluster = az aks list --resource-group $ResourceGroup --query "[0].name" -o tsv 2>&1
        if (-not $AksCluster -or $LASTEXITCODE -ne 0) {
            throw "Could not auto-detect AKS cluster in resource group '$ResourceGroup'."
        }
        Write-Ok "Detected cluster: $AksCluster"
    }
}

Invoke-StepCommand "az aks get-credentials --resource-group $ResourceGroup --name $AksCluster --overwrite-existing" {
    az aks get-credentials --resource-group $ResourceGroup --name $AksCluster --overwrite-existing -ErrorAction Stop
}

# ---------------------------------------------------------------------------
# 4. Build, push, render, apply — per service
# ---------------------------------------------------------------------------

$results = @()

foreach ($service in $Services) {
    Write-Step "Deploying service: $service"

    $imageTag = "demo-$timestamp"
    $fullImage = "$acrFqdn/${service}:$imageTag"
    $dockerfilePath = Join-Path $repoRoot "apps\$service\src\Dockerfile"
    $buildContext = Join-Path $repoRoot "apps\$service\src"

    if (-not (Test-Path $dockerfilePath)) {
        Write-Host "  [WARN] Dockerfile not found at $dockerfilePath — skipping $service" -ForegroundColor Red
        $results += [PSCustomObject]@{ Service = $service; ImageTag = "N/A"; Status = "SKIPPED (no Dockerfile)" }
        continue
    }

    # -- Build --
    if ($SkipBuild) {
        Write-Skip "Docker build (--SkipBuild)"
    } else {
        Invoke-StepCommand "docker build -t $fullImage --target prod -f $dockerfilePath $buildContext" {
            docker build -t $fullImage --target prod -f $dockerfilePath $buildContext
        }
        Write-Ok "Built $fullImage"
    }

    # -- Push --
    if ($SkipPush -or $SkipBuild) {
        Write-Skip "Docker push (--SkipPush or --SkipBuild)"
    } else {
        Invoke-StepCommand "docker push $fullImage" {
            docker push $fullImage
        }
        Write-Ok "Pushed $fullImage"
    }

    # -- Render Helm --
    Invoke-StepCommand "Render Helm chart for $service" {
        $env:IMAGE_PREFIX = "$acrFqdn/$service"
        $env:IMAGE_TAG = $imageTag
        $env:DEPLOY_ENV = "dev"
        $env:PUBLICATION_MODE = "agc"

        & $renderHelmScript -ServiceName $service

        Remove-Item Env:\IMAGE_PREFIX -ErrorAction SilentlyContinue
        Remove-Item Env:\IMAGE_TAG -ErrorAction SilentlyContinue
        Remove-Item Env:\DEPLOY_ENV -ErrorAction SilentlyContinue
        Remove-Item Env:\PUBLICATION_MODE -ErrorAction SilentlyContinue
    }

    # -- Apply --
    $manifestPath = Join-Path $repoRoot ".kubernetes\rendered\$service\all.yaml"
    if ($DryRun) {
        Write-Dry "kubectl apply -f $manifestPath -n $Namespace"
    } else {
        if (-not (Test-Path $manifestPath)) {
            throw "Rendered manifest not found at $manifestPath"
        }
        kubectl apply -f $manifestPath -n $Namespace -ErrorAction Stop
        Write-Ok "Applied manifest for $service"
    }

    $results += [PSCustomObject]@{ Service = $service; ImageTag = $imageTag; Status = "APPLIED" }
}

# ---------------------------------------------------------------------------
# 5. Wait for rollouts
# ---------------------------------------------------------------------------

Write-Step "Waiting for rollouts"

foreach ($entry in $results | Where-Object { $_.Status -eq "APPLIED" }) {
    $svc = $entry.Service
    if ($DryRun) {
        Write-Dry "kubectl rollout status deployment/$svc -n $Namespace --timeout=120s"
        $entry.Status = "DRY-RUN OK"
    } else {
        Write-Host "  Waiting for $svc..." -ForegroundColor Gray
        $rolloutOutput = kubectl rollout status "deployment/$svc" -n $Namespace --timeout=120s 2>&1
        if ($LASTEXITCODE -eq 0) {
            $entry.Status = "ROLLED OUT"
            Write-Ok "$svc rollout complete"
        } else {
            $entry.Status = "ROLLOUT TIMEOUT"
            Write-Host "  [WARN] $svc rollout timed out: $rolloutOutput" -ForegroundColor Red
        }
    }
}

# ---------------------------------------------------------------------------
# 6. Deployment summary
# ---------------------------------------------------------------------------

Write-Step "Deployment Summary"

Write-Host ""
Write-Host ("  {0,-35} {1,-25} {2}" -f "SERVICE", "IMAGE TAG", "STATUS") -ForegroundColor White
Write-Host ("  {0,-35} {1,-25} {2}" -f "-------", "---------", "------") -ForegroundColor DarkGray
foreach ($entry in $results) {
    $color = switch ($entry.Status) {
        "ROLLED OUT"  { "Green" }
        "DRY-RUN OK"  { "Magenta" }
        default       { "Yellow" }
    }
    Write-Host ("  {0,-35} {1,-25} {2}" -f $entry.Service, $entry.ImageTag, $entry.Status) -ForegroundColor $color
}
Write-Host ""
Write-Host "  Namespace:  $Namespace" -ForegroundColor Gray
Write-Host "  ACR:        $acrFqdn" -ForegroundColor Gray
Write-Host "  Cluster:    $AksCluster" -ForegroundColor Gray
Write-Host "  Timestamp:  $timestamp" -ForegroundColor Gray
Write-Host ""
