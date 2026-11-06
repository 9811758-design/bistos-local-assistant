param(
    [ValidateRange(1024, 65535)][int]$Port = 8765,
    [string]$LanAddress = '',
    [string]$TunnelHost = '',
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$dataRoot = Join-Path $projectRoot 'data'
$processRecordPath = Join-Path $dataRoot 'app-process.json'
$stableEnabledPath = Join-Path $dataRoot 'stable-access\enabled'
$explicitTunnelHost = $PSBoundParameters.ContainsKey('TunnelHost')
$canUseStableAccess = $Port -eq 8765 -and -not $LanAddress -and -not $explicitTunnelHost -and (Test-Path -LiteralPath $stableEnabledPath)
if ($canUseStableAccess) {
    $stableStartPath = Join-Path $PSScriptRoot 'stable-start.ps1'
    $psExe = Join-Path ([Environment]::GetFolderPath('System')) 'WindowsPowerShell\v1.0\powershell.exe'
    & $stableStartPath -NoBrowser | Out-Null
    . (Join-Path $PSScriptRoot 'stable-common.ps1')
    $watcher = Read-OwnedProcess $watchPath $psExe
    if (-not $watcher) {
        $watcherArgs = @('-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',"`"$(Join-Path $PSScriptRoot 'stable-watch.ps1')`"")
        $watcherProcess = Start-Process -FilePath $psExe -ArgumentList $watcherArgs -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $stableRoot 'watch.stdout.log') `
            -RedirectStandardError (Join-Path $stableRoot 'watch.stderr.log')
        $null = $watcherProcess.Handle
    }
    $stableConfig = Get-Content -LiteralPath (Join-Path $dataRoot 'stable-access\config.json') -Raw | ConvertFrom-Json
    $stableUrl = "https://$($stableConfig.hostname)"
    if (-not $NoBrowser) { Start-Process $stableUrl }
    Write-Output $stableUrl
    exit 0
}
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Python environment is missing. Run uv sync in the application folder.'
}
# Windows PowerShell can decode native stdout with the active code page. Keep
# the path transport ASCII so Korean installation paths remain exact.
$basePythonPathEncoded = (& $pythonPath -c 'import base64,sys; print(base64.b64encode(sys._base_executable.encode()).decode())').Trim()
$basePythonPath = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($basePythonPathEncoded))
New-Item -ItemType Directory -Force -Path $dataRoot | Out-Null
$address = '127.0.0.1'
$scheme = 'http'
if ($LanAddress -and $TunnelHost) { throw 'TunnelHost cannot be used together with LanAddress.' }
if ($TunnelHost -and $TunnelHost -cnotmatch '^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.(trycloudflare\.com|ngrok-free\.dev)$') {
    throw 'TunnelHost must be an exact lowercase *.trycloudflare.com or *.ngrok-free.dev hostname.'
}
if ($LanAddress) {
    $validatedAddress = [System.Net.IPAddress]::Parse($LanAddress)
    $address = $validatedAddress.ToString()
    $scheme = 'https'
}
$appUrl = "${scheme}://${address}:$Port"
$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    $owned = $false
    if (Test-Path -LiteralPath $processRecordPath) {
        $record = Get-Content -LiteralPath $processRecordPath -Raw | ConvertFrom-Json
        $running = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
        if ($running -and $running.Path -in @($pythonPath, $basePythonPath) -and
            $listener.OwningProcess -contains $running.Id -and
            $running.StartTime.ToUniversalTime().Ticks -eq
                ([datetime]$record.started_at).ToUniversalTime().Ticks) {
            $owned = $true
            $recordTunnel = [string]$record.tunnel_host
            if ($PSBoundParameters.ContainsKey('TunnelHost') -and $recordTunnel -ne $TunnelHost) {
                throw "The owned application is already running with tunnel host '$recordTunnel'. Stop it before changing the remote mode."
            }
            $appUrl = $record.url
        }
    }
    if (-not $owned) { throw "Port $Port is in use by another program." }
    if (-not $NoBrowser) { Start-Process $appUrl }
    Write-Output $appUrl
    exit 0
}
$modelStart = Join-Path $PSScriptRoot 'model-start.ps1'
if (Test-Path -LiteralPath (Join-Path $projectRoot 'runtime\ollama\ollama.exe')) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $modelStart
    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'Local AI could not start. Manual search remains available.'
    }
}
$storageRoot = $dataRoot
if (Test-Path -LiteralPath (Join-Path $dataRoot 'storage-location.json') -PathType Leaf) {
    $storageRoot = & (Join-Path $PSScriptRoot 'data-location.ps1') -ProjectRoot $projectRoot
}
$env:BISTOS_DATA_DIR = $storageRoot
$bundledCorpus = Join-Path $projectRoot 'knowledge\bistos-manuals'
if (Test-Path -LiteralPath (Join-Path $bundledCorpus 'manifest.json') -PathType Leaf) {
    $env:BISTOS_CORPUS_DIR = $bundledCorpus
}
$env:BISTOS_PORT = "$Port"
$env:BISTOS_LAN = '0'
if ($TunnelHost) { $env:BISTOS_TUNNEL_HOST = $TunnelHost } else { Remove-Item Env:BISTOS_TUNNEL_HOST -ErrorAction SilentlyContinue }
if ($LanAddress) {
    & $pythonPath -m bistos.tls $address
    if ($LASTEXITCODE -ne 0) { throw 'TLS certificate creation failed.' }
    $env:BISTOS_LAN = '1'
    $env:BISTOS_BIND_ADDRESS = $address
    $env:BISTOS_TLS_CERT = Join-Path $storageRoot 'tls\server.crt'
    $env:BISTOS_TLS_KEY = Join-Path $storageRoot 'tls\server.key'
    $env:BISTOS_ALLOWED_HOSTS = "127.0.0.1,localhost,$address"
}
$server = Start-Process -FilePath $pythonPath -ArgumentList @('-m', 'bistos') `
    -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $dataRoot 'server.stdout.log') `
    -RedirectStandardError (Join-Path $dataRoot 'server.stderr.log')
$ready = $false
for ($attempt = 0; $attempt -lt 45; $attempt++) {
    if ($server.HasExited) { throw "Program stopped. See $dataRoot\server.stderr.log" }
    $open = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($socket in $open) {
        $candidate = Get-CimInstance Win32_Process -Filter "ProcessId=$($socket.OwningProcess)"
        if ($candidate -and
            ($candidate.ProcessId -eq $server.Id -or $candidate.ParentProcessId -eq $server.Id) -and
            $candidate.ExecutablePath -in @($pythonPath, $basePythonPath)) {
            $listenerProcess = Get-Process -Id $candidate.ProcessId
            [ordered]@{
                pid = $listenerProcess.Id
                launcher_pid = $server.Id
                started_at = $listenerProcess.StartTime.ToUniversalTime().ToString('o')
                url = $appUrl
                tunnel_host = $TunnelHost
                python = $listenerProcess.Path
                data_dir = $storageRoot
            } | ConvertTo-Json | Set-Content -LiteralPath $processRecordPath -Encoding UTF8
            $ready = $true
            break
        }
    }
    if ($ready) { break }
    Start-Sleep -Seconds 1
}
if (-not $ready) { throw "Startup is taking longer than expected. See $dataRoot\server.stderr.log" }
if (-not $NoBrowser) { Start-Process $appUrl }
Write-Output $appUrl
