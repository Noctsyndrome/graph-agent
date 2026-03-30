$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$logsDir = Join-Path $repoRoot "logs"
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

$ports = 8000, 8501
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
$uiOut = Join-Path $logsDir "ui_$stamp.out.log"
$uiErr = Join-Path $logsDir "ui_$stamp.err.log"

Start-Process -FilePath $python `
    -ArgumentList @("-m", "uvicorn", "kgqa.api:app", "--host", "127.0.0.1", "--port", "8000") `
    -WorkingDirectory $repoRoot `
    -RedirectStandardOutput $apiOut `
    -RedirectStandardError $apiErr | Out-Null

Start-Sleep -Seconds 3

Start-Process -FilePath $python `
    -ArgumentList @("-m", "streamlit", "run", "ui/app.py", "--server.address", "127.0.0.1", "--server.port", "8501", "--server.headless", "true", "--browser.gatherUsageStats", "false") `
    -WorkingDirectory $repoRoot `
    -RedirectStandardOutput $uiOut `
    -RedirectStandardError $uiErr | Out-Null

Write-Host "API log: $apiOut"
Write-Host "UI log:  $uiOut"
