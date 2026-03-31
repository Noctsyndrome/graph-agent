$ErrorActionPreference = "Stop"

$ports = 8000, 5173
foreach ($port in $ports) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($conns) {
        $ownerPids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
        foreach ($procId in $ownerPids) {
            try {
                Stop-Process -Id $procId -Force -ErrorAction Stop
                Write-Host "Stopped process $procId on port $port"
            } catch {
            }
        }
    }
}
