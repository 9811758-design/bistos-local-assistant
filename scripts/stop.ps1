param([switch]$Qa)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$recordPath = Join-Path $projectRoot 'data\app-process.json'
if ($Qa) { $recordPath = Join-Path $projectRoot 'output\qa\process.json' }
$expectedPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
# Windows PowerShell can decode native stdout with the active code page. Keep
# the path transport ASCII so Korean installation paths remain exact.
$basePythonPathEncoded = (& $expectedPython -c 'import base64,sys; print(base64.b64encode(sys._base_executable.encode()).decode())').Trim()
$basePythonPath = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($basePythonPathEncoded))
if (-not (Test-Path -LiteralPath $recordPath)) { Write-Output 'Program is not running.'; exit 0 }
$record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
$running = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
if ($running) {
    if ($running.Path -notin @($expectedPython, $basePythonPath) -or
        $running.StartTime.ToUniversalTime().Ticks -ne
            ([datetime]$record.started_at).ToUniversalTime().Ticks) {
        throw 'Process ownership differs. No process was stopped.'
    }
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$($running.Id)"
    foreach ($child in $children) {
        if ($child.ExecutablePath -eq $basePythonPath -and $child.CommandLine -match ' -m bistos(?: |$)') {
            Stop-Process -Id $child.ProcessId
        }
    }
    Stop-Process -Id $running.Id -ErrorAction SilentlyContinue
}
Remove-Item -LiteralPath $recordPath
Write-Output 'Program stopped.'
