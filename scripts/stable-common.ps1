$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$stableRoot = Join-Path $projectRoot 'data\stable-access'
$statePath = Join-Path $stableRoot 'process.json'
$watchPath = Join-Path $stableRoot 'watch-process.json'
$enabledPath = Join-Path $stableRoot 'enabled'
$ngrokPath = Join-Path $projectRoot 'runtime\ngrok\ngrok.exe'
$psExe = Join-Path ([Environment]::GetFolderPath('System')) 'WindowsPowerShell\v1.0\powershell.exe'
# PowerShell 7 can pass its module path to a shortcut-launched Windows PowerShell.
# These isolated launchers use only the built-in Windows modules.
$env:PSModulePath = "$(Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'WindowsPowerShell\Modules');$(Join-Path (Split-Path -Parent $psExe) 'Modules')"
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$sha = [Security.Cryptography.SHA256]::Create()
try { $projectKey = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($projectRoot.ToLowerInvariant()))).Replace('-', '').Substring(0, 16) } finally { $sha.Dispose() }
$lockName = "Local\BistosStable-$sid-$projectKey"

function Invoke-StableScript([string]$ScriptPath, [string[]]$ScriptArguments = @(), [int]$TimeoutSeconds = 90) {
    # Wait for the launcher itself, not inherited pipes kept open by its server children.
    $label = [IO.Path]::GetFileNameWithoutExtension($ScriptPath)
    $arguments = @('-NoProfile','-ExecutionPolicy','Bypass','-File',"`"$ScriptPath`"") + $ScriptArguments
    $launcher = Start-Process -FilePath $psExe -ArgumentList $arguments -WindowStyle Hidden -PassThru `
        -WorkingDirectory $projectRoot `
        -RedirectStandardOutput (Join-Path $stableRoot "$label.stdout.log") `
        -RedirectStandardError (Join-Path $stableRoot "$label.stderr.log")
    $null = $launcher.Handle
    if (-not $launcher.WaitForExit($TimeoutSeconds * 1000)) {
        Stop-Process -Id $launcher.Id -ErrorAction SilentlyContinue
        throw "Launcher timeout: $label"
    }
    if ($launcher.ExitCode -ne 0) { throw "Launcher failed: $label (see local stderr log)" }
}

function Enter-StableLock([string]$Name, [int]$Milliseconds = 60000) {
    $lock = New-Object Threading.Mutex($false, $Name)
    try { $acquired = $lock.WaitOne($Milliseconds) } catch [Threading.AbandonedMutexException] { $acquired = $true }
    if (-not $acquired) { $lock.Dispose(); return $null }
    return $lock
}

function Read-OwnedProcess([string]$RecordPath, [string]$ExpectedPath) {
    if (-not (Test-Path -LiteralPath $RecordPath)) { return $null }
    try {
        $record = Get-Content -LiteralPath $RecordPath -Raw | ConvertFrom-Json
        $process = Get-Process -Id ([int]$record.pid) -ErrorAction Stop
        if ($process.Path -ne $ExpectedPath -or
            $process.StartTime.ToUniversalTime().Ticks -ne ([datetime]$record.started_at).ToUniversalTime().Ticks) { return $null }
        return [pscustomobject]@{ Process = $process; Record = $record }
    } catch { return $null }
}

function Stop-OwnedProcess([string]$RecordPath, [string]$ExpectedPath) {
    $owned = Read-OwnedProcess $RecordPath $ExpectedPath
    if ($owned) {
        Stop-Process -Id $owned.Process.Id -ErrorAction Stop
        $owned.Process.WaitForExit(10000) | Out-Null
        if (-not $owned.Process.HasExited) { throw 'Owned process did not stop.' }
    }
    if (Test-Path -LiteralPath $RecordPath) { Remove-Item -LiteralPath $RecordPath -Force }
}

function Read-StableConfig([string]$AgentConfigPath) {
    $publicPath = Join-Path $stableRoot 'config.json'
    $config = Get-Content -LiteralPath $publicPath -Raw | ConvertFrom-Json
    if ([string]$config.hostname -cnotmatch '^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.ngrok-free\.dev$') { throw 'Invalid fixed hostname.' }
    if ([string]$config.binary_sha256 -cnotmatch '^[0-9a-fA-F]{64}$') { throw 'Missing verified ngrok hash.' }
    if ((Get-FileHash -LiteralPath $ngrokPath -Algorithm SHA256).Hash -ne $config.binary_sha256) { throw 'ngrok binary hash mismatch.' }
    # JSON is valid YAML and can be checked without adding a YAML dependency.
    if (-not (Test-Path -LiteralPath $AgentConfigPath -PathType Leaf)) { throw 'Agent configuration file is missing; its contents were not logged.' }
    try { $agentConfig = Get-Content -LiteralPath $AgentConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { throw 'Agent configuration could not be read as UTF-8 JSON; its contents were not logged.' }
    $endpoint = @($agentConfig.endpoints)
    $unexpected = @($agentConfig.PSObject.Properties.Name | Where-Object { $_ -notin @('version','agent','endpoints') })
    $unexpected += @($agentConfig.agent.PSObject.Properties.Name | Where-Object { $_ -notin @('authtoken','console_ui','inspect_db_size','web_addr','remote_management','update_check','log_format','log_level') })
    foreach ($item in $endpoint) {
        $unexpected += @($item.PSObject.Properties.Name | Where-Object { $_ -notin @('name','url','upstream') })
        $unexpected += @($item.upstream.PSObject.Properties.Name | Where-Object { $_ -ne 'url' })
    }
    if ($unexpected.Count) { throw 'Agent config contains unexpected fields or request rewriting.' }
    if ($agentConfig.version -ne 3 -or $endpoint.Count -ne 1 -or $endpoint[0].name -cne 'bistos' -or
        $endpoint[0].url -cne "https://$($config.hostname)" -or
        $endpoint[0].upstream.url -cne 'http://127.0.0.1:8765') { throw 'Agent endpoint must match the fixed hostname and local Bistos port.' }
    if (-not $agentConfig.agent.authtoken -or $agentConfig.agent.inspect_db_size -ne -1 -or
        $agentConfig.agent.web_addr -ne $false -or $agentConfig.agent.remote_management -ne $false -or
        $agentConfig.agent.update_check -ne $false -or $agentConfig.agent.log_level -ne 'info' -or
        $agentConfig.agent.log_format -ne 'json') { throw 'Agent authentication or privacy settings are incomplete.' }
    return $config
}
