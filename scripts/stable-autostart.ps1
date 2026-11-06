[CmdletBinding(DefaultParameterSetName='Status')]
param(
    [Parameter(Mandatory=$true,ParameterSetName='Install')][switch]$Install,
    [Parameter(Mandatory=$true,ParameterSetName='Remove')][switch]$Remove,
    [Parameter(ParameterSetName='Status')][switch]$Status
)
. (Join-Path $PSScriptRoot 'stable-common.ps1')
$linkPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'Bistos stable access.lnk'
$watchScript = Join-Path $PSScriptRoot 'stable-watch.ps1'
$shell = New-Object -ComObject WScript.Shell
if ($Install) {
    Read-StableConfig (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'BistosRemote\ngrok.yml') | Out-Null
    if (Test-Path -LiteralPath $linkPath) {
        $oldLink = $shell.CreateShortcut($linkPath)
        if ($oldLink.Arguments -notlike "*`"$watchScript`"*") { throw 'A startup shortcut with this name belongs to another installation.' }
    }
    $link = $shell.CreateShortcut($linkPath)
    $link.TargetPath = $psExe
    $link.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$watchScript`""
    $link.WorkingDirectory = $projectRoot
    $link.Save()
    Set-Content -LiteralPath $enabledPath -Value 'enabled' -Encoding ASCII
    Write-Output 'Enabled after this Windows user signs in.'
} elseif ($Remove) {
    if (Test-Path -LiteralPath $enabledPath) { Remove-Item -LiteralPath $enabledPath -Force }
    if (Test-Path -LiteralPath $linkPath) {
        $link = $shell.CreateShortcut($linkPath)
        if ($link.Arguments -notlike "*`"$watchScript`"*") { throw 'The shortcut belongs to another installation.' }
        Remove-Item -LiteralPath $linkPath -Force
    }
    Write-Output 'Automatic startup disabled.'
} else {
    [ordered]@{ installed=(Test-Path -LiteralPath $linkPath); enabled=(Test-Path -LiteralPath $enabledPath); trigger='current-user Windows sign-in' } | ConvertTo-Json
}
