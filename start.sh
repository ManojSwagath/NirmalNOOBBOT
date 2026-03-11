#!/bin/bash
# ============================================================
#  MoodBot — Single-command launcher
#  Works on: Linux Mint, Raspberry Pi 5, Ubuntu, Debian
#
#  ONE-LINER (copy-paste into any fresh Linux terminal):
#
#    git clone https://github.com/ManojSwagath/NirmalNOOBBOT.git && cd NirmalNOOBBOT && bash start.sh YOUR_GROQ_API_KEY
#
#  Usage:
#    bash start.sh <GROQ_API_KEY>     # first time — pass your key
#    bash start.sh                     # after that — key is saved
#
#  First run installs everything automatically.
#  Subsequent runs skip straight to launch (~2 seconds).
# ============================================================

set -e
cd "$(dirname "$0")"

GROQ_KEY="${1:-}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo ""
echo "============================================"
echo "  MoodBot — AI Emotion Companion"
echo "============================================"
echo ""

# ── 1. System packages (only if missing) ─────────────────────────────────────

NEED_APT=0
for cmd in espeak-ng python3; do
    if ! command -v "$cmd" &>/dev/null; then NEED_APT=1; break; fi
done
# Also check dev libraries needed for PyAudio / OpenCV build
if ! dpkg -s portaudio19-dev &>/dev/null 2>&1; then NEED_APT=1; fi

if [ "$NEED_APT" -eq 1 ]; then
    echo -e "${YELLOW}[SETUP] Installing system packages (needs sudo)...${NC}"
    sudo apt-get update -qq
    sudo apt-get install -y -qq \
        python3 python3-pip python3-venv \
        espeak-ng libespeak-ng1 \
        libatlas-base-dev \
        libgtk-3-dev \
        portaudio19-dev \
        libjpeg-dev libpng-dev libtiff-dev \
        v4l-utils 2>/dev/null
    echo -e "${GREEN}[SETUP] System packages done.${NC}"
else
    echo -e "${GREEN}[OK] System packages already installed.${NC}"
fi

# ── 2. Python virtual environment ────────────────────────────────────────────

if [ ! -d "venv" ]; then
    echo -e "${YELLOW}[SETUP] Creating virtual environment...${NC}"
    python3 -m venv venv
fi

source venv/bin/activate

# ── 3. Python dependencies (skip if already satisfied) ───────────────────────

MARKER="venv/.deps_installed"
if [ ! -f "$MARKER" ]; then
    echo -e "${YELLOW}[SETUP] Installing Python packages (one-time)...${NC}"
    pip install --quiet --upgrade pip
    pip install --quiet -r requirements-pi.txt
    touch "$MARKER"
    echo -e "${GREEN}[SETUP] Python packages done.${NC}"
else
    echo -e "${GREEN}[OK] Python packages already installed.${NC}"
fi

# ── 4. GROQ API key ──────────────────────────────────────────────────────────

if [ -n "$GROQ_KEY" ]; then
    echo "GROQ_API_KEY=$GROQ_KEY" > .env
    echo -e "${GREEN}[OK] API key saved to .env${NC}"
elif [ ! -f ".env" ]; then
    echo ""
    echo -e "${RED}[MISSING] GROQ API key not provided!${NC}"
    echo ""
    read -rp "  Paste your GROQ_API_KEY: " key
    if [ -z "$key" ]; then
        echo -e "${RED}  No key entered. Get one at https://console.groq.com${NC}"
        exit 1
    fi
    echo "GROQ_API_KEY=$key" > .env
    echo -e "${GREEN}  Saved to .env${NC}"
else
    echo -e "${GREEN}[OK] API key already configured.${NC}"
fi

# ── 5. Launch ────────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}[START] Launching MoodBot...${NC}"
echo "  Press Q in the video window to quit."
echo ""
python main.py
