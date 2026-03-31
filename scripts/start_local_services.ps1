$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$logsDir = Join-Path $repoRoot "logs"
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

$ports = 8000, 5173
foreach ($port in $ports) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($conns) {
        $ownerPids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
        foreach ($procId in $ownerPids) {
            try {
                Stop-Process -Id $procId -Force -ErrorAction Stop
            } catch {
            }
        }
    }
}

Start-Sleep -Seconds 2

$venvPython = Join-Path $repoRoot ".venv\\Scripts\\python.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$apiOut = Join-Path $logsDir "api_$stamp.out.log"
$apiErr = Join-Path $logsDir "api_$stamp.err.log"
$frontendOut = Join-Path $logsDir "frontend_$stamp.out.log"
$frontendErr = Join-Path $logsDir "frontend_$stamp.err.log"

Start-Process -FilePath $python `
    -ArgumentList @("-m", "uvicorn", "kgqa.api:app", "--host", "127.0.0.1", "--port", "8000") `
    -WorkingDirectory $repoRoot `
    -RedirectStandardOutput $apiOut `
    -RedirectStandardError $apiErr | Out-Null

Start-Sleep -Seconds 3

$npm = if (Get-Command npm.cmd -ErrorAction SilentlyContinue) { "npm.cmd" } else { "npm" }
$frontendRoot = Join-Path $repoRoot "frontend"

Start-Process -FilePath $npm `
    -ArgumentList @("run", "dev", "--", "--host", "127.0.0.1", "--port", "5173") `
    -WorkingDirectory $frontendRoot `
    -RedirectStandardOutput $frontendOut `
    -RedirectStandardError $frontendErr | Out-Null

function Wait-HttpReady {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url,
        [int]$TimeoutSeconds = 30
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 5
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                return $true
            }
        } catch {
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

$apiReady = Wait-HttpReady -Url "http://127.0.0.1:8000/health" -TimeoutSeconds 30
$frontendReady = Wait-HttpReady -Url "http://127.0.0.1:5173" -TimeoutSeconds 30

Write-Host "API log: $apiOut"
Write-Host "UI log:  $frontendOut"
Write-Host "API ready: $apiReady"
Write-Host "UI ready:  $frontendReady"
