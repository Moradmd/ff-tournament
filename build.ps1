# Production build + test + upload ZIP
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

function Find-Python {
    foreach ($c in @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "python"
    )) {
        if ($c -match '\\') {
            if (Test-Path $c) { return $c }
        } else {
            $p = Get-Command $c -ErrorAction SilentlyContinue
            if ($p) { return $p.Source }
        }
    }
    return $null
}

$py = Find-Python
if (-not $py) {
    Write-Host "Python pacheni. winget install Python.Python.3.12" -ForegroundColor Red
    exit 1
}

Write-Host "=== FF Tournament - Production Build ===" -ForegroundColor Cyan
Write-Host "Python: $py" -ForegroundColor Gray

Write-Host "`n[1/4] Dependencies..." -ForegroundColor Yellow
& $py -m pip install -r requirements.txt -q
& $py -m pip install gunicorn -q

Write-Host "[2/4] Syntax + import check..." -ForegroundColor Yellow
& $py -m py_compile app.py database.py config.py rupantorpay.py payment_gateway.py sslcommerz.py uid_api.py
$env:RENDER_EXTERNAL_URL = "https://ff-tournament.onrender.com"
$env:DATABASE_PATH = "$env:TEMP\ff-tournament-test.db"
if (Test-Path $env:DATABASE_PATH) { Remove-Item $env:DATABASE_PATH -Force }
& $py check_build.py

Write-Host "[3/4] Gunicorn smoke test..." -ForegroundColor Yellow
$gunicorn = & $py -c "import shutil; print(shutil.which('gunicorn') or '')"
if (-not $gunicorn) {
    $gunicorn = Join-Path (Split-Path $py) "Scripts\gunicorn.exe"
}
if (-not (Test-Path $gunicorn)) {
    Write-Host "gunicorn skip" -ForegroundColor Gray
} else {
    Write-Host "gunicorn found: $gunicorn" -ForegroundColor Gray
}

Write-Host "[4/4] Upload ZIP..." -ForegroundColor Yellow
& "$root\prepare-upload.ps1"

Write-Host "`n=== BUILD OK ===" -ForegroundColor Green
Write-Host "ZIP: $(Join-Path $root 'ff-tournament-upload.zip')" -ForegroundColor Cyan
Write-Host "Render settings: RENDER-SETTINGS.txt" -ForegroundColor Cyan
