[CmdletBinding()]
param(
    [string]$AgentConfigPath = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'BistosRemote\ngrok.yml'),
    [switch]$NoBrowser,
    [switch]$ValidateOnly,
    [switch]$FromWatcher
)
. (Join-Path $PSScriptRoot 'stable-common.ps1')
$operationLock = Enter-StableLock "$lockName-operation"
if (-not $operationLock) { throw 'Another stable access operation is still running.' }
try {
    if ($FromWatcher -and -not (Test-Path -LiteralPath $enabledPath)) { exit 0 }
    $config = Read-StableConfig $AgentConfigPath
    $url = "https://$($config.hostname)"
    if ($ValidateOnly) { Write-Output 'Stable configuration validated.'; exit 0 }
    New-Item -ItemType Directory -Path $stableRoot -Force | Out-Null
    $owned = Read-OwnedProcess $statePath $ngrokPath
    if ($owned -and ($owned.Record.hostname -cne $config.hostname -or $owned.Record.url -cne $url)) { throw 'Stop the existing fixed tunnel before changing its hostname.' }
    if (-not $owned) {
        Write-Verbose 'Checking for an unrecorded connector.'
        $orphans = Get-CimInstance Win32_Process -Filter "Name='ngrok.exe'" | Where-Object { $_.ExecutablePath -eq $ngrokPath }
        if ($orphans) { throw 'Unrecorded project ngrok process exists; ownership must be checked before replacing it.' }
    }
    Write-Verbose 'Starting local AI.'
    Invoke-StableScript (Join-Path $PSScriptRoot 'model-start.ps1')
    # A legacy local shortcut may already have started our app without a tunnel.
    # Migrate only the owned local instance after its active questions finish.
    $appRecordPath = Join-Path $projectRoot 'data\app-process.json'
    if (Test-Path -LiteralPath $appRecordPath) {
        $appRecord = Get-Content -LiteralPath $appRecordPath -Raw | ConvertFrom-Json
        $listeners = @(Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue)
        if (-not [string]$appRecord.tunnel_host -and $appRecord.url -ceq 'http://127.0.0.1:8765' -and
            $listeners.OwningProcess -contains [int]$appRecord.pid) {
            $localPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
            $busyCode = "import json,sqlite3,sys; db=sqlite3.connect(sys.argv[1]); print(sum(json.loads(row[0]).get('status') in ('queued','running') for row in db.execute('SELECT payload FROM jobs'))); db.close()"
            $storageRoot = Join-Path $projectRoot 'data'
            if (Test-Path -LiteralPath (Join-Path $storageRoot 'storage-location.json') -PathType Leaf) {
                $storageRoot = & (Join-Path $PSScriptRoot 'data-location.ps1') -ProjectRoot $projectRoot
            }
            $busyCount = & $localPython -c $busyCode (Join-Path $storageRoot 'bistos.sqlite3')
            if ($LASTEXITCODE -ne 0 -or $busyCount -notmatch '^\d+$') { throw 'Cannot verify active questions before recovering remote mode.' }
            if ([int]$busyCount -gt 0) { throw 'Waiting for active questions before recovering remote mode.' }
            # stop.ps1 checks the executable path, PID and process start time.
            Invoke-StableScript (Join-Path $PSScriptRoot 'stop.ps1') @() 30
        }
    }
    Write-Verbose 'Starting Bistos.'
    Invoke-StableScript (Join-Path $PSScriptRoot 'start.ps1') @('-TunnelHost',$config.hostname,'-NoBrowser')
    Write-Verbose 'Checking the fixed hostname through the local application.'
    $status = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/status' `
        -Headers @{ Host = [string]$config.hostname } -TimeoutSec 10
    if ($status.setup_required) { throw 'Existing Bistos accounts were not found; remote startup stopped.' }
    if (-not $owned) {
        $stdoutPath = Join-Path $stableRoot 'ngrok.stdout.log'
        $stderrPath = Join-Path $stableRoot 'ngrok.stderr.log'
        $oldEnv = @{}
        foreach ($item in @(Get-ChildItem Env: | Where-Object { $_.Name -like 'NGROK_*' })) {
            $oldEnv[$item.Name] = $item.Value
            Remove-Item "Env:$($item.Name)"
        }
        $proc = $null
        try {
            Write-Verbose 'Starting the fixed connector.'
            $arguments = @('start', 'bistos', "--config=`"$AgentConfigPath`"", '--log=stdout', '--log-format=json', '--log-level=info')
            $proc = Start-Process -FilePath $ngrokPath -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
                -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
            [ordered]@{ pid=$proc.Id; started_at=$proc.StartTime.ToUniversalTime().ToString('o'); path=$ngrokPath; hostname=$config.hostname; url=$url } |
                ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
            # Checking the local log avoids spending public request allowance on health polling.
            $connected = $false
            for ($attempt = 0; $attempt -lt 45; $attempt++) {
                $proc.Refresh()
                if ($proc.HasExited) { throw 'ngrok exited before connecting; see its local error log.' }
                foreach ($line in @(Get-Content -LiteralPath $stdoutPath -Tail 30 -ErrorAction SilentlyContinue)) {
                    try { $entry = $line | ConvertFrom-Json } catch { continue }
                    if ($entry.msg -eq 'started tunnel' -and $entry.url -ceq $url) { $connected = $true; break }
                }
                if ($connected) { break }
                Start-Sleep -Seconds 1
            }
            if (-not $connected) { throw 'ngrok did not confirm the fixed endpoint within 45 seconds.' }
        } catch {
            if ($proc) { Stop-OwnedProcess $statePath $ngrokPath }
            throw
        } finally {
            foreach ($name in $oldEnv.Keys) { Set-Item "Env:$name" $oldEnv[$name] }
        }
    }
    if (-not $NoBrowser) { Start-Process $url }
    Write-Output $url
} finally {
    $operationLock.ReleaseMutex()
    $operationLock.Dispose()
}
