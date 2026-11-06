[CmdletBinding(DefaultParameterSetName = 'EmailProtected')]
param(
    [Parameter(Mandatory = $true, ParameterSetName = 'EmailProtected')][string[]]$AllowedEmail,
    [Parameter(Mandatory = $true, ParameterSetName = 'AppAccount')][switch]$UseAppLoginOnly,
    [ValidateSet(8765)][int]$Port = 8765,
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$dataRoot = Join-Path $projectRoot 'data'
$remoteRoot = Join-Path $dataRoot 'remote-access'
$configPath = Join-Path $remoteRoot 'config.json'
$processRecordPath = Join-Path $remoteRoot 'cloudflared-process.json'
$logPath = Join-Path $remoteRoot 'cloudflared.log'
$errorLogPath = Join-Path $remoteRoot 'cloudflared.error.log'
$cloudflaredPath = Join-Path $projectRoot 'runtime\cloudflared\cloudflared.exe'
$startScript = Join-Path $PSScriptRoot 'start.ps1'
$stopScript = Join-Path $PSScriptRoot 'stop.ps1'
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$authMode = 'email'
if ($PSCmdlet.ParameterSetName -eq 'AppAccount') {
    if (-not $UseAppLoginOnly) { throw 'App-account mode must be explicitly enabled.' }
    # This opt-in publishes the app login page. Existing app accounts remain mandatory.
    # Activate only after the owner has agreed to replace the email gateway.
    $authMode = 'app-account'
}
if (-not (Test-Path -LiteralPath $cloudflaredPath -PathType Leaf)) { throw 'cloudflared.exe is missing from runtime\cloudflared.' }
$hash = (Get-FileHash -LiteralPath $cloudflaredPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($hash -ne '4d2490e74082c5d48dbfedaf92576fa5bd1702379ab88eb7faa0cfbe8dc891b6') { throw 'cloudflared.exe hash does not match the verified release.' }
$help = (& $cloudflaredPath tunnel --help 2>&1 | Out-String)
if ($help -notmatch '--allowed-mail') { throw 'This cloudflared build lacks Protected Quick Tunnel --allowed-mail support.' }
$emails = @()
if ($authMode -eq 'email') {
    $emails = @($AllowedEmail | ForEach-Object { $_.Trim().ToLowerInvariant() } | Where-Object { $_ })
    if ($emails.Count -lt 1 -or $emails.Count -gt 5) { throw 'Provide one to five allowed email addresses.' }
}
foreach ($email in $emails) {
    if ($email -notmatch '^[A-Za-z0-9._%+-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$') { throw "Invalid allowed email: $email" }
}
New-Item -ItemType Directory -Force -Path $remoteRoot | Out-Null
$runningRecord = $null
if (Test-Path -LiteralPath $processRecordPath) { $runningRecord = Get-Content -LiteralPath $processRecordPath -Raw | ConvertFrom-Json }
if ($runningRecord) {
    $running = Get-Process -Id $runningRecord.pid -ErrorAction SilentlyContinue
    if ($running -and $running.Path -eq $cloudflaredPath -and $running.StartTime.ToUniversalTime().Ticks -eq ([datetime]$runningRecord.started_at).ToUniversalTime().Ticks) {
        $saved = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json -ErrorAction Stop
        $savedAuthMode = [string]$saved.auth_mode
        if (-not $savedAuthMode) { $savedAuthMode = 'email' }
        $savedEmails = @($saved.allowed_emails | ForEach-Object { [string]$_ })
        if ($savedAuthMode -eq $authMode -and $saved.port -eq $Port -and (@($savedEmails | Where-Object { $emails -notcontains $_ }).Count -eq 0) -and (@($emails | Where-Object { $savedEmails -notcontains $_ }).Count -eq 0)) {
            $appRecord = Join-Path $dataRoot 'app-process.json'
            if ((Test-Path -LiteralPath $appRecord) -and ((Get-Content -LiteralPath $appRecord -Raw | ConvertFrom-Json).tunnel_host -eq $runningRecord.host)) {
                & $startScript -Port $Port -TunnelHost $runningRecord.host -NoBrowser | Out-Null
                $status = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/status" -TimeoutSec 10
                if ($status.setup_required) { throw 'The application has not been initialized locally.' }
                Write-Output $runningRecord.url
                exit 0
            }
        }
        throw 'An owned tunnel is already running with different settings. Stop it before changing the remote access configuration.'
    }
    Remove-Item -LiteralPath $processRecordPath -Force
}
$unexpected = Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" |
    Where-Object { $_.ExecutablePath -eq $cloudflaredPath }
if ($unexpected) { throw 'The project cloudflared is running without a valid ownership record.' }
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Python environment is missing.' }
$dbPath = Join-Path $dataRoot 'bistos.sqlite3'
$busy = (& $pythonPath -c "import json,sqlite3,sys; db=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True); rows=db.execute('SELECT payload FROM jobs').fetchall(); print('busy' if any(json.loads(r[0]).get('status') in ('queued','running') for r in rows) else 'idle')" $dbPath 2>$null).Trim()
if ($LASTEXITCODE -ne 0 -or $busy -ne 'idle') { throw 'Could not prove that no queued or running question exists; remote mode was not changed.' }
$appRecordPath = Join-Path $dataRoot 'app-process.json'
$appWasRunning = Test-Path -LiteralPath $appRecordPath
Set-Content -LiteralPath $logPath -Value '' -Encoding UTF8
Set-Content -LiteralPath $errorLogPath -Value '' -Encoding UTF8
$args = @('--no-autoupdate','--config','cloudflared.yml','tunnel','--url',"http://127.0.0.1:$Port",'--management-diagnostics=false','--metrics','127.0.0.1:0')
foreach ($email in $emails) { $args += @('--allowed-mail',$email) }
Set-Content -LiteralPath (Join-Path $remoteRoot 'cloudflared.yml') -Value "{}" -Encoding ASCII
$savedEnv = @{}
foreach ($item in Get-ChildItem Env:) {
    if ($item.Name -match '^(TUNNEL_|CLOUDFLARED_)' -or $item.Name -in @('NO_TLS_VERIFY','HTTP_HOST_HEADER')) { $savedEnv[$item.Name] = $item.Value; Remove-Item "Env:$($item.Name)" -ErrorAction SilentlyContinue }
}
$tunnel = $null
try {
 if ($appWasRunning) { & $stopScript | Out-Null }
 $tunnel = Start-Process -FilePath $cloudflaredPath -ArgumentList $args -WorkingDirectory $remoteRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $logPath -RedirectStandardError $errorLogPath
 [ordered]@{ pid=$tunnel.Id; started_at=$tunnel.StartTime.ToUniversalTime().ToString('o'); url=''; host=''; path=$cloudflaredPath } | ConvertTo-Json | Set-Content -LiteralPath $processRecordPath -Encoding UTF8
$url = $null
for ($i = 0; $i -lt 45; $i++) {
    if ($tunnel.HasExited) { throw "cloudflared stopped. See $logPath" }
    $match = @(Select-String -LiteralPath @($logPath,$errorLogPath) -Pattern 'https://([a-z0-9-]+\.trycloudflare\.com)' -AllMatches) | Select-Object -Last 1
    if ($match) { $url = 'https://' + $match.Matches[$match.Matches.Count - 1].Groups[1].Value; break }
    Start-Sleep -Seconds 1
}
if (-not $url) { throw "No temporary URL was reported. See $logPath" }
$hostName = ([uri]$url).Host
    & $startScript -Port $Port -TunnelHost $hostName -NoBrowser
    if ($LASTEXITCODE -ne 0) { throw 'Application start failed.' }
    $status = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/status" -TimeoutSec 10
    if ($status.setup_required) { throw 'Initialize the application locally before sharing it.' }
[ordered]@{ pid=$tunnel.Id; started_at=$tunnel.StartTime.ToUniversalTime().ToString('o'); url=$url; host=$hostName; path=$cloudflaredPath } | ConvertTo-Json | Set-Content -LiteralPath $processRecordPath -Encoding UTF8
@{ auth_mode=$authMode; allowed_emails=$emails; port=$Port; url=$url; host=$hostName; created_at=[datetime]::UtcNow.ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding UTF8
} catch {
 $failure = $_
 if ($tunnel -and -not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force -ErrorAction SilentlyContinue }
 if ($tunnel -and (Test-Path -LiteralPath $processRecordPath)) { Remove-Item -LiteralPath $processRecordPath }
 if ($appWasRunning) {
     if (Test-Path -LiteralPath $appRecordPath) { & $stopScript | Out-Null }
     & $startScript -Port $Port -NoBrowser | Out-Null
 }
 throw $failure
} finally {
 foreach ($name in $savedEnv.Keys) { Set-Item "Env:$name" $savedEnv[$name] }
}
if (-not $NoBrowser) { Start-Process $url }
Write-Output $url
