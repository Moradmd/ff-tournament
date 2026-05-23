# ZIP banay hosting upload er jonno (.env / DB chara)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
# Keep the ZIP inside project folder so it's easy to find/upload
$zip = Join-Path $root "ff-tournament-upload.zip"

if (Test-Path $zip) { Remove-Item $zip -Force }

$exclude = @('.env', 'tournament.db', '__pycache__', '.venv', 'venv')
$files = Get-ChildItem -Path $root -Recurse -File | Where-Object {
    $rel = $_.FullName.Substring($root.Length + 1)
    $skip = $false
    foreach ($e in $exclude) {
        if ($rel -eq $e -or $rel.StartsWith("$e\") -or $rel.Contains("\$e\")) { $skip = $true; break }
    }
    -not $skip
}

$temp = Join-Path $env:TEMP "ff-tournament-upload"
if (Test-Path $temp) { Remove-Item $temp -Recurse -Force }
New-Item -ItemType Directory -Path $temp | Out-Null

foreach ($f in $files) {
    $rel = $f.FullName.Substring($root.Length + 1)
    $dest = Join-Path $temp $rel
    $dir = Split-Path $dest -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    Copy-Item $f.FullName $dest
}

Compress-Archive -Path (Join-Path $temp "*") -DestinationPath $zip -Force
Remove-Item $temp -Recurse -Force

Write-Host ""
Write-Host "ZIP ready:" -ForegroundColor Green
Write-Host "  $zip" -ForegroundColor Cyan
Write-Host ""
Write-Host "Render: RENDER-SETTINGS.txt khule copy-paste koro" -ForegroundColor Yellow
Write-Host "Guide: DEPLOY.md" -ForegroundColor Yellow
