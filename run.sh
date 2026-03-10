#!/bin/bash
# ============================================================
#  MoodBot — One-click launcher for Raspberry Pi / Linux (uses uv)
#  Installs system deps + uv + Python deps (first run) and starts.
# ============================================================

set -e
cd "$(dirname "$0")"

echo "============================================"
echo "  MoodBot — AI Emotion Companion"
echo "============================================"

# Install system dependencies if espeak-ng is not found
if ! command -v espeak-ng &>/dev/null; then
    echo "[SETUP] Installing system dependencies (needs sudo)..."
    sudo apt update
    sudo apt install -y \
        python3-pip python3-venv \
        espeak-ng libespeak-ng1 \
        libatlas-base-dev libhdf5-dev \
        libgtk-3-dev libopencv-dev \
        portaudio19-dev python3-pyaudio \
        libjpeg-dev libpng-dev libtiff-dev
fi

# Install uv if not available
if ! command -v uv &>/dev/null; then
    echo "[SETUP] Installing uv package manager..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# Create venv with uv if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "[SETUP] Creating virtual environment with uv..."
    uv venv
fi

# Activate venv
source .venv/bin/activate

# Install / update Python dependencies
echo "[SETUP] Installing Python dependencies with uv..."
uv pip install -r requirements-pi.txt

# Check .env
if [ ! -f ".env" ]; then
    echo ""
    echo "[ERROR] .env file not found!"
    echo "  Create it with:  echo 'GROQ_API_KEY=your_key_here' > .env"
    echo ""
    exit 1
fi

# Run the app
echo ""
echo "[START] Launching MoodBot..."
echo "  Press Q in the video window to quit."
echo ""
python main.py
