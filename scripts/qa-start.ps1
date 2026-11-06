$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$qaRoot = Join-Path $projectRoot 'output\qa'
New-Item -ItemType Directory -Path $qaRoot -Force | Out-Null
$env:BISTOS_DATA_DIR = Join-Path $qaRoot 'data'
$env:BISTOS_PORT = '8876'
$env:BISTOS_LAN = '0'
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$server = Start-Process -FilePath $pythonPath -ArgumentList @('-m', 'bistos') `
    -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $qaRoot 'server.stdout.log') `
    -RedirectStandardError (Join-Path $qaRoot 'server.stderr.log')
@{
    pid = $server.Id
    started_at = $server.StartTime.ToUniversalTime().ToString('o')
    python = $pythonPath
    data_dir = $env:BISTOS_DATA_DIR
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $qaRoot 'process.json') -Encoding UTF8
Write-Output "QA server PID $($server.Id), http://127.0.0.1:8876"
