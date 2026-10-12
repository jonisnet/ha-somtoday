param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot "..\..\downloads")
)

$ErrorActionPreference = "Stop"

$helperDirectory = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$manifest = Get-Content (Join-Path $helperDirectory "manifest.json") -Raw | ConvertFrom-Json
$version = $manifest.version
$archiveName = "Somtoday-Login-Helper-$version.zip"
$outputDirectoryPath = [System.IO.Path]::GetFullPath($OutputDirectory)
$archivePath = Join-Path $outputDirectoryPath $archiveName
$temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("somtoday-login-helper-" + [guid]::NewGuid().ToString("N"))

$packageFiles = @(
    "manifest.json",
    "background.js",
    "oauth_redirect.js",
    "popup.html",
    "popup.js",
    "popup.css",
    "INSTALLEREN-WINDOWS.cmd",
    "LEESMIJ-EERST.txt",
    "README.md",
    "SECURITY.md"
)

try {
    New-Item -ItemType Directory -Path $temporaryRoot | Out-Null
    New-Item -ItemType Directory -Force -Path $outputDirectoryPath | Out-Null

    foreach ($file in $packageFiles) {
        $source = Join-Path $helperDirectory $file
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
            throw "Ontbrekend pakketbestand: $source"
        }
        Copy-Item -LiteralPath $source -Destination $temporaryRoot
    }

    if (Test-Path -LiteralPath $archivePath) {
        Remove-Item -LiteralPath $archivePath
    }

    Compress-Archive -Path (Join-Path $temporaryRoot "*") -DestinationPath $archivePath -CompressionLevel Optimal

    $archive = [System.IO.Compression.ZipFile]::OpenRead($archivePath)
    try {
        $entryNames = @($archive.Entries | ForEach-Object FullName)
        foreach ($file in $packageFiles) {
            if ($entryNames -notcontains $file) {
                throw "ZIP-controle mislukt: $file ontbreekt"
            }
        }
    }
    finally {
        $archive.Dispose()
    }

    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archivePath).Hash.ToLowerInvariant()
    Write-Output $archivePath
    Write-Output "SHA256 $hash"
}
finally {
    if (Test-Path -LiteralPath $temporaryRoot) {
        Remove-Item -LiteralPath $temporaryRoot -Recurse -Force
    }
}

