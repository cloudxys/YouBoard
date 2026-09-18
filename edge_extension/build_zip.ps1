# Package the extension into a zip that Microsoft Edge Add-ons accepts.
# manifest.json must sit at the ROOT of the archive (this script guarantees it).
#
# NOTE: this file is intentionally ASCII-only: Windows PowerShell 5.1 reads
# .ps1 files as ANSI unless they carry a UTF-8 BOM, which would mangle any
# non-ASCII text and break parsing.
#
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File build_zip.ps1

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$manifestPath = Join-Path $here 'manifest.json'
if (-not (Test-Path -LiteralPath $manifestPath)) { throw "manifest.json not found" }

# Read as UTF-8 (PowerShell 5.1's Get-Content -Raw would decode the Chinese
# text as ANSI and break JSON parsing), then pull the version with a regex.
$manifestText = [IO.File]::ReadAllText($manifestPath, [Text.Encoding]::UTF8)
$match = [regex]::Match($manifestText, '"version"\s*:\s*"([^"]+)"')
if (-not $match.Success) { throw "version not found in manifest.json" }
$version = $match.Groups[1].Value
Write-Host ("Manifest version: {0}" -f $version)
$out = Join-Path $here ("YouBoard_Companion_v{0}.zip" -f $version)
if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Force }

$staging = Join-Path $env:TEMP ("yb_ext_" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $staging | Out-Null
foreach ($item in @('manifest.json', 'background.js', 'content.js',
                    'panel.html', 'panel.css', 'panel.js',
                    'options.html', 'options.css', 'options.js')) {
    Copy-Item -LiteralPath (Join-Path $here $item) -Destination $staging
}
Copy-Item -LiteralPath (Join-Path $here 'icons') -Destination $staging -Recurse

Compress-Archive -Path (Join-Path $staging '*') -DestinationPath $out -Force
Remove-Item -LiteralPath $staging -Recurse -Force

Write-Host ("Packed: {0}  ({1:N0} bytes)" -f $out, (Get-Item -LiteralPath $out).Length)
Write-Host "Upload this zip to Partner Center (manifest.json is at the archive root)."
