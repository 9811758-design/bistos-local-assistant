$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$shell = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktop '비스토스 제품도우미.lnk'
$shortcut = $shell.CreateShortcut($shortcutPath)
$legacyTarget = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$legacyArguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$(Join-Path $PSScriptRoot 'start.ps1')`""
$exePath = Join-Path $projectRoot 'BistosProductAssistant.exe'
$useExe = Test-Path -LiteralPath $exePath
$target = if ($useExe) { $exePath } else { $legacyTarget }
$arguments = if ($useExe) { '' } else { $legacyArguments }
$isOwnedLegacy = $shortcut.TargetPath -eq $legacyTarget -and $shortcut.Arguments -eq $legacyArguments
$isOwnedExe = $shortcut.TargetPath -eq $exePath -and [string]::IsNullOrEmpty($shortcut.Arguments)
if ((Test-Path -LiteralPath $shortcutPath) -and -not ($isOwnedLegacy -or $isOwnedExe)) {
    throw 'A different shortcut already uses this name. Existing shortcut was not changed.'
}
$shortcut.TargetPath = $target
$shortcut.Arguments = $arguments
$shortcut.WorkingDirectory = $projectRoot
$shortcut.WindowStyle = if ($useExe) { 1 } else { 7 }
$shortcut.IconLocation = if ($useExe) { "$exePath,0" } else { "$legacyTarget,0" }
$shortcut.Description = '비스토스 매뉴얼 검색 및 로컬 AI 원문 안내'
$shortcut.Save()
Write-Output $shortcutPath
