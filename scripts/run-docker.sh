#!/bin/bash
# ============================================================
#  MoodBot — Docker launcher for Linux / Raspberry Pi
#
#  ONE-LINE SETUP (paste into terminal):
#
#    git clone https://github.com/ManojSwagath/NirmalNOOBBOT.git && cd NirmalNOOBBOT && bash scripts/run-docker.sh YOUR_GROQ_API_KEY
#
#  Nothing is installed on your system except Docker.
#  Everything runs inside the container.
# ============================================================

set -e
cd "$(dirname "$0")/.."

GROQ_KEY="${1:-}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo ""
echo "============================================"
echo "  MoodBot — Docker Launch"
echo "============================================"
echo ""

# ── 1. Install Docker if missing ─────────────────────────────────────────────

if ! command -v docker &>/dev/null; then
    echo -e "${YELLOW}[SETUP] Docker not found. Installing...${NC}"
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker "$USER"
    echo -e "${GREEN}[SETUP] Docker installed.${NC}"
    echo -e "${YELLOW}[INFO] You need to log out and log back in (or reboot) for Docker permissions.${NC}"
    echo "  Then run:  bash scripts/run-docker.sh $GROQ_KEY"
    exit 0
fi

# If user can't talk to Docker daemon, add to group
if ! docker info &>/dev/null 2>&1; then
    echo -e "${YELLOW}[INFO] Adding you to the docker group (needs sudo)...${NC}"
    sudo usermod -aG docker "$USER"
    echo -e "${YELLOW}[INFO] Log out and back in, then re-run this script.${NC}"
    exit 0
fi

# ── 2. GROQ API key ──────────────────────────────────────────────────────────

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

# ── 3. X11 + Audio setup ─────────────────────────────────────────────────────

# Allow X11 connections from Docker containers
xhost +local: 2>/dev/null || true

# Create data dir for persistent memory
mkdir -p data

export DISPLAY=${DISPLAY:-:0}
export XAUTHORITY=${XAUTHORITY:-$HOME/.Xauthority}

# PulseAudio for speaker/mic passthrough
mkdir -p ~/.config/pulse
pactl load-module module-native-protocol-unix auth-anonymous=1 2>/dev/null || true

# ── 4. Build and run ─────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}[BUILD] Building Docker image (first time takes a few minutes)...${NC}"
echo ""
docker compose up --build
