[CmdletBinding()]
param()
. (Join-Path $PSScriptRoot 'stable-common.ps1')
# Stop retries first. Wait for any in-flight start before stopping the tunnel.
if (Test-Path -LiteralPath $enabledPath) { Remove-Item -LiteralPath $enabledPath -Force }
Stop-OwnedProcess $watchPath $psExe
$operationLock = Enter-StableLock "$lockName-operation"
if (-not $operationLock) { throw 'A startup operation is still finishing. Retry stable-stop.' }
try {
    Stop-OwnedProcess $statePath $ngrokPath
    $remaining = Get-CimInstance Win32_Process -Filter "Name='ngrok.exe'" | Where-Object { $_.ExecutablePath -eq $ngrokPath }
    if ($remaining) { throw 'An unrecorded project ngrok process remains. Remote access is not confirmed stopped.' }
    Write-Output 'Fixed remote access stopped. The local app and AI remain available.'
} finally {
    $operationLock.ReleaseMutex()
    $operationLock.Dispose()
}
