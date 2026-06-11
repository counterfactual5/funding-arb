# Funding Arb Dashboard — Windows Launch Script
#
# Usage:
#   .\start.ps1              # Browser mode
#   .\start.ps1 -Desktop     # Desktop app mode (Tauri)
#   .\start.ps1 -ApiOnly     # API backend only
#   .\start.ps1 -BuildWeb    # Build frontend only

param(
    [switch]$Desktop,
    [switch]$ApiOnly,
    [switch]$BuildWeb,
    [switch]$Help
)

Set-Location $PSScriptRoot

if ($Help) {
    Write-Host ""
    Write-Host "  Funding Arb Dashboard Launch Script"
    Write-Host ""
    Write-Host "  Usage:"
    Write-Host "    .\start.ps1              Browser mode (build frontend + start server)"
    Write-Host "    .\start.ps1 -Desktop     Desktop app mode (Tauri, requires Rust)"
    Write-Host "    .\start.ps1 -ApiOnly     API backend only"
    Write-Host "    .\start.ps1 -BuildWeb    Build frontend only"
    Write-Host ""
    exit 0
}

function Check-Python {
    $script:Python = $null
    if (Get-Command python3 -ErrorAction SilentlyContinue) {
        $script:Python = "python3"
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $script:Python = "python"
    } else {
        Write-Host "  Python is not installed. Please install Python 3.10+" -ForegroundColor Red
        exit 1
    }
    Write-Host "  [OK] Python: $(python --version)" -ForegroundColor Green
}

function Check-Node {
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
        Write-Host "  Node.js is not installed. Please install Node.js 18+" -ForegroundColor Red
        exit 1
    }
    Write-Host "  [OK] Node.js: $(node --version)" -ForegroundColor Green
}

function Check-Rust {
    if (-not (Get-Command rustc -ErrorAction SilentlyContinue)) {
        Write-Host "  Rust is not installed. Please visit: https://rustup.rs" -ForegroundColor Red
        exit 1
    }
    Write-Host "  [OK] Rust: $(rustc --version)" -ForegroundColor Green
}

function Install-PythonDeps {
    $has = & $Python -c "import fastapi" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  -> Installing Python dependencies..." -ForegroundColor Cyan
        & $Python -m pip install -q fastapi "uvicorn[standard]" websockets requests
    }
    Write-Host "  [OK] Python dependencies ready" -ForegroundColor Green
}

function Install-NodeDeps {
    if (-not (Test-Path "web\node_modules")) {
        Write-Host "  -> Installing Node.js dependencies..." -ForegroundColor Cyan
        Set-Location web; npm install; Set-Location ..
    }
    Write-Host "  [OK] Node.js dependencies ready" -ForegroundColor Green
}

function Build-Web {
    Write-Host "  -> Building frontend..." -ForegroundColor Cyan
    Set-Location web; npm run build; Set-Location ..
    Write-Host "  [OK] Frontend build complete -> web\dist\" -ForegroundColor Green
}

# Desktop mode
if ($Desktop) {
    Write-Host ""
    Write-Host "  ========================================"
    Write-Host "   Funding Arb -- Desktop App Mode"
    Write-Host "  ========================================"
    Write-Host ""
    Check-Python; Check-Node; Check-Rust
    Install-PythonDeps; Install-NodeDeps
    Write-Host "  -> Starting Tauri desktop app..." -ForegroundColor Cyan
    Set-Location web; npm run tauri dev
    exit 0
}

# API only mode
if ($ApiOnly) {
    Write-Host ""
    Write-Host "  ========================================"
    Write-Host "   Funding Arb -- API Only Mode"
    Write-Host "  ========================================"
    Write-Host ""
    Check-Python; Install-PythonDeps
    Write-Host "  -> Starting API server..." -ForegroundColor Cyan
    & $Python -m uvicorn server.main:app --host 0.0.0.0 --port 8787
    exit 0
}

# Build web only
if ($BuildWeb) {
    Write-Host ""
    Write-Host "  ========================================"
    Write-Host "   Funding Arb -- Build Frontend"
    Write-Host "  ========================================"
    Write-Host ""
    Check-Node; Install-NodeDeps; Build-Web
    Write-Host "  [OK] Done. Run .\start.ps1 to start in browser mode" -ForegroundColor Green
    exit 0
}

# Default: browser mode
Write-Host ""
Write-Host "  ========================================"
Write-Host "   Funding Arb -- Browser Mode"
Write-Host "  ========================================"
Write-Host ""
Check-Python; Check-Node
Install-PythonDeps; Install-NodeDeps

if (-not (Test-Path "web\dist\index.html")) {
    Write-Host "  [!] Frontend not built, building now..." -ForegroundColor Yellow
    Build-Web
}

Write-Host "  -> Starting server..." -ForegroundColor Cyan
Write-Host ""
& $Python server\main.py --no-reload
