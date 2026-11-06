$ErrorActionPreference = 'Stop'
$runtimeRoot = Join-Path (Split-Path -Parent $PSScriptRoot) 'runtime'
$pidPath = Join-Path $runtimeRoot 'server-process.json'
if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Output 'No project Ollama process record exists.'
    exit 0
}
$record = Get-Content -LiteralPath $pidPath -Raw | ConvertFrom-Json
$server = Get-Process -Id $record.process_id -ErrorAction SilentlyContinue
if (-not $server) {
    Write-Output 'The recorded project Ollama process has already stopped.'
    exit 0
}
if ($server.Path -ne $record.binary_path -or
    $server.StartTime.ToUniversalTime().Ticks -ne
        ([datetime]$record.process_start_time_utc).ToUniversalTime().Ticks) {
    throw 'Recorded process identity has changed; no process was stopped.'
}
Stop-Process -Id $server.Id
Write-Output "Stopped project Ollama PID $($server.Id)."
