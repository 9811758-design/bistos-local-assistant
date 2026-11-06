param([string]$OutputDirectory = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputDirectory) { $OutputDirectory = $projectRoot }
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
$launcherRoot = Join-Path $projectRoot 'launcher'
$assetRoot = Join-Path $launcherRoot 'assets'
$logoPath = Join-Path $assetRoot 'bistos-logo.png'
$iconPath = Join-Path $assetRoot 'bistos-logo.ico'
$buildRoot = Join-Path $projectRoot 'output\build-launcher'
New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
$installationPath = Join-Path $buildRoot 'InstallationRoot.txt'
[IO.File]::WriteAllText($installationPath, $projectRoot, (New-Object Text.UTF8Encoding($false)))

# Convert the complete official logo into Windows icon frames without redrawing it.
Add-Type -AssemblyName System.Drawing
$source = [Drawing.Image]::FromFile($logoPath)
$frames = New-Object 'Collections.Generic.List[byte[]]'
$sizes = @(16,24,32,48,64,128,256)
try {
    foreach ($size in $sizes) {
        $bitmap = New-Object Drawing.Bitmap($size, $size)
        $graphics = [Drawing.Graphics]::FromImage($bitmap)
        $memory = New-Object IO.MemoryStream
        try {
            $graphics.Clear([Drawing.Color]::White)
            $graphics.InterpolationMode = [Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $graphics.PixelOffsetMode = [Drawing.Drawing2D.PixelOffsetMode]::HighQuality
            $padding = [Math]::Max(1, [int]($size * 0.04))
            $width = $size - 2 * $padding
            $height = [Math]::Max(1, [int]($width * $source.Height / $source.Width))
            $top = [int](($size - $height) / 2)
            $graphics.DrawImage($source, $padding, $top, $width, $height)
            $bitmap.Save($memory, [Drawing.Imaging.ImageFormat]::Png)
            $frames.Add($memory.ToArray())
        } finally {
            $memory.Dispose()
            $graphics.Dispose()
            $bitmap.Dispose()
        }
    }
} finally { $source.Dispose() }
$stream = [IO.File]::Create($iconPath)
$writer = New-Object IO.BinaryWriter($stream)
try {
    $writer.Write([uint16]0)
    $writer.Write([uint16]1)
    $writer.Write([uint16]$sizes.Count)
    $offset = 6 + 16 * $sizes.Count
    for ($index = 0; $index -lt $sizes.Count; $index++) {
        $dimension = if ($sizes[$index] -eq 256) { 0 } else { $sizes[$index] }
        $writer.Write([byte]$dimension)
        $writer.Write([byte]$dimension)
        $writer.Write([byte]0)
        $writer.Write([byte]0)
        $writer.Write([uint16]1)
        $writer.Write([uint16]32)
        $writer.Write([uint32]$frames[$index].Length)
        $writer.Write([uint32]$offset)
        $offset += $frames[$index].Length
    }
    foreach ($frame in $frames) { $writer.Write($frame) }
} finally {
    $writer.Dispose()
    $stream.Dispose()
}
$compiler = Join-Path ([Environment]::GetFolderPath('Windows')) 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw 'The Windows .NET Framework compiler was not found.' }
$exePath = Join-Path $outputRoot 'BistosProductAssistant.exe'
$compileArgs = @(
    '/nologo', '/target:winexe', '/platform:anycpu', '/optimize+',
    '/reference:System.Drawing.dll', '/reference:System.Windows.Forms.dll',
    '/reference:System.Web.Extensions.dll',
    "/win32manifest:$(Join-Path $launcherRoot 'app.manifest')",
    "/win32icon:$iconPath",
    "/resource:$logoPath,BistosLauncher.BistosLogo.png",
    "/resource:$installationPath,BistosLauncher.InstallationRoot.txt",
    "/out:$exePath",
    (Join-Path $launcherRoot 'BistosLauncher.cs')
)
& $compiler @compileArgs
if ($LASTEXITCODE -ne 0) { throw 'Executable compilation failed.' }
Write-Output $exePath
