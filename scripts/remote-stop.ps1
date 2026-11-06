$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$remoteRoot = Join-Path $projectRoot 'data\remote-access'
$recordPath = Join-Path $remoteRoot 'cloudflared-process.json'
$startScript = Join-Path $PSScriptRoot 'start.ps1'
$stopScript = Join-Path $PSScriptRoot 'stop.ps1'
$cloudflaredPath = Join-Path $projectRoot 'runtime\cloudflared\cloudflared.exe'
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$dataRoot = Join-Path $projectRoot 'data'
$appRecordPath = Join-Path $dataRoot 'app-process.json'
$remoteWasActive = $false
if (Test-Path -LiteralPath $recordPath) {
    $record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
    $running = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
    if ($running) {
        if ($running.Path -ne $cloudflaredPath -or $running.StartTime.ToUniversalTime().Ticks -ne ([datetime]$record.started_at).ToUniversalTime().Ticks) { throw 'cloudflared process ownership differs. No process was stopped.' }
        Stop-Process -Id $running.Id
        $remoteWasActive = $true
    }
    Remove-Item -LiteralPath $recordPath -Force
}
if (Test-Path -LiteralPath $appRecordPath) {
    $appRecord = Get-Content -LiteralPath $appRecordPath -Raw | ConvertFrom-Json
    if ($appRecord.tunnel_host) { $remoteWasActive = $true }
}
$unexpected = Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" |
    Where-Object { $_.ExecutablePath -eq $cloudflaredPath }
if ($unexpected) { throw 'The project cloudflared still runs without a verified ownership record. Remote stop is incomplete.' }
if (-not $remoteWasActive) {
    Write-Output 'Remote access is not running.'; exit 0
}
$busy = (& $pythonPath -c "import json,sqlite3,sys; db=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True); rows=db.execute('SELECT payload FROM jobs').fetchall(); print('busy' if any(json.loads(r[0]).get('status') in ('queued','running') for r in rows) else 'idle')" (Join-Path $dataRoot 'bistos.sqlite3') 2>$null).Trim()
if ($LASTEXITCODE -ne 0 -or $busy -ne 'idle') { throw 'Could not prove that no queued or running question exists; local mode was not restored.' }
& $stopScript | Out-Null
& $startScript -NoBrowser | Out-Null
Write-Output 'Remote access stopped; local access restored at http://127.0.0.1:8765.'
