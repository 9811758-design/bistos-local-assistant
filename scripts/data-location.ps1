param([Parameter(Mandatory=$true)][string]$ProjectRoot)
$locationFile = Join-Path $ProjectRoot 'data\storage-location.json'
if (-not (Test-Path -LiteralPath $locationFile -PathType Leaf)) {
    return (Join-Path $ProjectRoot 'data')
}
$location = Get-Content -LiteralPath $locationFile -Raw -Encoding UTF8 | ConvertFrom-Json
if (@($location.PSObject.Properties.Name).Count -ne 1 -or
    $location.PSObject.Properties.Name -notcontains 'data_dir' -or
    $location.data_dir -isnot [string] -or
    $location.data_dir -notmatch '^[A-Za-z]:[\\/]' -or
    $location.data_dir.StartsWith('\\') -or $location.data_dir.StartsWith('//')) {
    throw 'Storage must be an absolute local directory.'
}
$resolved = [IO.Path]::GetFullPath($location.data_dir)
if (-not (Test-Path -LiteralPath (Join-Path $resolved 'bistos.sqlite3') -PathType Leaf)) {
    throw 'Configured data is missing. Refusing to start with an empty account store.'
}
return $resolved
