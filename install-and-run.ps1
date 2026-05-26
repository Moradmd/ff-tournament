# FF Tournament — install dependencies and start server
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Find-Python {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "py",
        "python",
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "C:\Program Files\Python312\python.exe",
        "C:\Program Files\Python313\python.exe"
    )
    foreach ($c in $candidates) {
        try {
            if ($c -match '\\') {
                if (Test-Path $c) { return $c }
            } else {
                $p = Get-Command $c -ErrorAction SilentlyContinue
                if ($p) {
                    $v = & $p.Source --version 2>&1
                    if ($v -match 'Python 3\.') { return $p.Source }
                }
            }
        } catch {}
    }
    return $null
}

$py = Find-Python
if (-not $py) {
    Write-Host "Python pacheni. Prothome Python 3.12 install koro:" -ForegroundColor Yellow
    Write-Host "  winget install Python.Python.3.12" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Install er por PowerShell bandho kore abar khulo, tarpor:" -ForegroundColor Yellow
    Write-Host "  .\install-and-run.ps1" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Windows Settings > Apps > Advanced > App execution aliases:" -ForegroundColor Yellow
    Write-Host "  'python.exe' ar 'python3.exe' OFF koro (Store stub avoid)" -ForegroundColor Yellow
    exit 1
}

Write-Host "Using: $py" -ForegroundColor Green
& $py -m pip install --upgrade pip -q
& $py -m pip install -r requirements.txt -q
Write-Host ""
$lanIp = $null
try {
    $lanIp = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notmatch '^127\.' -and $_.IPAddress -notmatch '^169\.254' } |
        Select-Object -First 1 -ExpandProperty IPAddress
} catch {}
if ($lanIp) {
    Write-Host "Phone (same WiFi): http://${lanIp}:5000/" -ForegroundColor Cyan
}
Write-Host "PC:  http://127.0.0.1:5000/  (Admin: /admin)" -ForegroundColor Green
Write-Host "Bandh korar jonno Ctrl+C" -ForegroundColor Gray
& $py app.py
