$ErrorActionPreference = "Stop"

Write-Host "=== FFmpeg-Cluster Windows Installer ===" -ForegroundColor Cyan

# Check FFmpeg
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Warning "FFmpeg was not found in PATH. Please install FFmpeg (e.g. via 'winget install Gyan.FFmpeg') before running FFmpeg-Cluster."
} else {
    Write-Host "[*] FFmpeg found in PATH." -ForegroundColor Green
}

# Find Python
$pythonExe = $null
if (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonExe = "python"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonExe = "py"
}

if (-not $pythonExe) {
    Write-Error "Python 3 was not found. Please install Python 3.10+ and try again."
    exit 1
}

$venvDir = ".\FFmpeg-Cluster-venv"
Write-Host "[*] Creating virtual environment in $venvDir..."
& $pythonExe -m venv $venvDir

$pipExe = Join-Path $venvDir "Scripts\pip.exe"
Write-Host "[*] Installing dependencies from requirements.txt..."
& $pipExe install --upgrade pip
& $pipExe install -r requirements.txt

Write-Host ""
Write-Host "=== Installation complete! ===" -ForegroundColor Green
Write-Host "Activate the environment with:"
Write-Host "  .\FFmpeg-Cluster-venv\Scripts\Activate.ps1"
Write-Host ""
Write-Host "Start the server:"
Write-Host "  python server.py --required_clients 2 --file_name input.mp4 --segment_request --segment_time 10"
Write-Host ""
Write-Host "Start a client:"
Write-Host "  python client.py --server_ip 127.0.0.1"
