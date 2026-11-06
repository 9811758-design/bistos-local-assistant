param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 11434
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $projectRoot 'runtime'
$binaryPath = Join-Path $runtimeRoot 'ollama\ollama.exe'
$pidPath = Join-Path $runtimeRoot 'server-process.json'
$logRoot = Join-Path $runtimeRoot 'logs'

if (-not (Test-Path -LiteralPath $binaryPath)) {
    throw 'Portable Ollama is missing. Restore runtime/ollama before starting.'
}

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    if (Test-Path -LiteralPath $pidPath) {
        $record = Get-Content -LiteralPath $pidPath -Raw | ConvertFrom-Json
        $running = Get-Process -Id $record.process_id -ErrorAction SilentlyContinue
        if ($running -and $running.Path -eq $binaryPath -and $record.port -eq $Port -and
            $running.StartTime.ToUniversalTime().Ticks -eq
                ([datetime]$record.process_start_time_utc).ToUniversalTime().Ticks -and
            $listener.OwningProcess -contains $running.Id) {
            Write-Output "Project Ollama already running at http://127.0.0.1:$Port"
            exit 0
        }
    }
    throw "Port $Port is already in use. Existing processes were not changed."
}

New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$env:OLLAMA_HOST = "127.0.0.1:$Port"
$env:OLLAMA_MODELS = Join-Path $runtimeRoot 'models'
$env:OLLAMA_NO_CLOUD = '1'
$env:OLLAMA_NUM_PARALLEL = '1'
# Keep the answer model and the small local embedding model resident together.
$env:OLLAMA_MAX_LOADED_MODELS = '2'
$env:OLLAMA_MAX_QUEUE = '8'
$env:OLLAMA_CONTEXT_LENGTH = '8192'
$env:OLLAMA_DEBUG = '0'
$env:HTTP_PROXY = ''
$env:HTTPS_PROXY = ''
$env:ALL_PROXY = ''
$env:NO_PROXY = '127.0.0.1,localhost'

$server = Start-Process -FilePath $binaryPath -ArgumentList 'serve' `
    -WorkingDirectory $runtimeRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $logRoot 'server.stdout.log') `
    -RedirectStandardError (Join-Path $logRoot 'server.stderr.log')

[ordered]@{
    process_id = $server.Id
    process_start_time_utc = $server.StartTime.ToUniversalTime().ToString('o')
    binary_path = $binaryPath
    port = $Port
    model_directory = $env:OLLAMA_MODELS
    cloud_enabled = $false
} | ConvertTo-Json | Set-Content -LiteralPath $pidPath -Encoding UTF8

Write-Output "Started project Ollama PID $($server.Id) at http://127.0.0.1:$Port"
