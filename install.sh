#!/usr/bin/env bash
set -e

echo "=== FFmpeg-Cluster Installer ==="

if command -v pkg >/dev/null 2>&1; then
    echo "[*] Detected Termux environment..."
    pkg update -y
    pkg install -y python ffmpeg
elif command -v apt-get >/dev/null 2>&1; then
    echo "[*] Detected Debian/Ubuntu environment..."
    sudo apt-get update -y
    sudo apt-get install -y python3 python3-pip python3-venv ffmpeg
elif command -v dnf >/dev/null 2>&1; then
    echo "[*] Detected Fedora/RHEL environment..."
    sudo dnf install -y python3 python3-pip ffmpeg
elif command -v pacman >/dev/null 2>&1; then
    echo "[*] Detected Arch Linux environment..."
    sudo pacman -Sy --noconfirm python python-pip ffmpeg
else
    echo "[!] Package manager not recognized. Please ensure Python 3 and FFmpeg are installed."
fi

VENV_DIR="./FFmpeg-Cluster-venv"
echo "[*] Creating Python virtual environment in ${VENV_DIR}..."
python3 -m venv "${VENV_DIR}"

echo "[*] Installing Python dependencies..."
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install -r requirements.txt

echo ""
echo "=== Installation complete! ==="
echo "Activate the environment with:"
echo "  source ${VENV_DIR}/bin/activate"
echo ""
echo "Start the server:"
echo "  python3 server.py --required_clients 2 --file_name input.mp4 --segment_request --segment_time 10"
echo ""
echo "Start a client:"
echo "  python3 client.py --server_ip 127.0.0.1"
