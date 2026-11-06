[CmdletBinding()]
param([ValidateRange(10,300)][int]$IntervalSeconds = 30)
. (Join-Path $PSScriptRoot 'stable-common.ps1')
$me = Get-Process -Id $PID
if ($me.Path -ne $psExe) { throw 'Run stable-watch with Windows PowerShell (powershell.exe).' }
$watchLock = Enter-StableLock "$lockName-watcher" 0
if (-not $watchLock) { exit 0 }
try {
    if (-not (Test-Path -LiteralPath $enabledPath)) { exit 0 }
    [ordered]@{ pid=$PID; started_at=$me.StartTime.ToUniversalTime().ToString('o'); path=$me.Path } |
        ConvertTo-Json | Set-Content -LiteralPath $watchPath -Encoding UTF8
    $lastStatus = ''
    $logPath = Join-Path $stableRoot 'watch.log'
    $agentConfigPath = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'BistosRemote\ngrok.yml'
    while (Test-Path -LiteralPath $enabledPath) {
        $status = 'ready'
        try {
            Invoke-StableScript (Join-Path $PSScriptRoot 'stable-start.ps1') @('-AgentConfigPath',('"{0}"' -f $agentConfigPath),'-NoBrowser','-FromWatcher') 150
        } catch { $status = 'retry: startup command failed' }
        if ($status -cne $lastStatus) {
            if ((Test-Path -LiteralPath $logPath) -and (Get-Item -LiteralPath $logPath).Length -gt 1MB) {
                Move-Item -LiteralPath $logPath -Destination (Join-Path $stableRoot 'watch.previous.log') -Force
            }
            "$(Get-Date -Format o) $status" | Add-Content -LiteralPath $logPath
            $lastStatus = $status
        }
        Start-Sleep -Seconds $IntervalSeconds
    }
} finally {
    $mine = Read-OwnedProcess $watchPath $me.Path
    if ($mine -and $mine.Process.Id -eq $PID) { Remove-Item -LiteralPath $watchPath -Force }
    $watchLock.ReleaseMutex()
    $watchLock.Dispose()
}
