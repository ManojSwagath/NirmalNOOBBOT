# Empathetic AI Companion — Connection & Deployment Guide

---

## Table of Contents
1. [Run Locally on Windows](#1-run-locally-on-windows)
2. [Deploy to Raspberry Pi 5](#2-deploy-to-raspberry-pi-5)
3. [Connect ESP32 + Motors](#3-connect-esp32--motors)
4. [Full Wiring Diagram](#4-full-wiring-diagram)
5. [Troubleshooting](#5-troubleshooting)

---

## 1. Run Locally on Windows

### Prerequisites
- Python 3.11 installed
- Webcam connected
- Microphone connected
- Internet connection (for Google Speech Recognition)

### Steps

```powershell
# 1. Navigate to project folder
cd d:\MoodBot

# 2. Activate virtual environment
& d:\MoodBot\.venv\Scripts\Activate.ps1

# 3. Run the app
python main.py
```

- Press **q** in the camera window to quit.
- Console will print detected emotions, spoken lines, and heard speech.

### Set VS Code Interpreter (to remove import errors)
`Ctrl+Shift+P` → `Python: Select Interpreter` → select `.venv`

---

## 2. Deploy to Raspberry Pi 5

### What you need
- Raspberry Pi 5 (4GB RAM)
- Raspberry Pi Camera Module (or USB webcam)
- USB microphone or USB speaker+mic combo
- MicroSD card with Raspberry Pi OS (64-bit, Bookworm recommended)
- Same local network as your Windows PC (for SCP file transfer)

---

### Step 1 — Install System Dependencies on RPi

SSH into your RPi, then run:

```bash
sudo apt update && sudo apt upgrade -y

sudo apt install -y \
    python3-pip python3-venv git \
    espeak-ng libespeak1 \
    libatlas-base-dev libhdf5-dev \
    portaudio19-dev \
    libgtk-3-dev \
    libcamera-apps \
    python3-opencv
```

---

### Step 2 — Copy Project Files from Windows to RPi

On your **Windows PC** (PowerShell):

```powershell
# Replace <RPI_IP> with your Raspberry Pi's IP address e.g. 192.168.1.50
scp -r d:\MoodBot pi@<RPI_IP>:/home/pi/MoodBot
```

To find your RPi's IP address, run this on the RPi terminal:
```bash
hostname -I
```

---

### Step 3 — Set Up Virtual Environment on RPi

```bash
cd /home/pi/MoodBot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> **Note:** TensorFlow install on RPi can take 10–20 minutes. Be patient.

---

### Step 4 — Reduce Load for Raspberry Pi

Edit these constants in `main.py` for better performance on RPi:

```python
FRAME_WIDTH      = 320   # was 640
FRAME_HEIGHT     = 240   # was 480
ANALYSE_EVERY_N  = 20    # was 10
```

---

### Step 5 — Run on Raspberry Pi

```bash
source /home/pi/MoodBot/.venv/bin/activate
python main.py
```

To run automatically on boot, add to `/etc/rc.local` before `exit 0`:
```bash
su pi -c "cd /home/pi/MoodBot && source .venv/bin/activate && python main.py &"
```

---

## 3. Connect ESP32 + Motors

### Overview
The Raspberry Pi sends emotion commands to the ESP32 over **USB Serial**.
The ESP32 drives the L298N motor driver based on received commands.

```
RPi  →  USB cable  →  ESP32  →  L298N  →  4x Motors
```

---

### ESP32 Arduino Firmware

Flash this sketch to the ESP32 using **Arduino IDE**:

```cpp
#include <Arduino.h>

// ── L298N Pin Mapping ──
#define ENA 14   // PWM speed control — Motor A
#define IN1 26   // Direction — Motor A
#define IN2 27   // Direction — Motor A
#define ENB 12   // PWM speed control — Motor B
#define IN3 25   // Direction — Motor B
#define IN4 33   // Direction — Motor B

void stopMotors() {
  analogWrite(ENA, 0);
  analogWrite(ENB, 0);
}

void moveForward() {
  digitalWrite(IN1, HIGH); digitalWrite(IN2, LOW);
  digitalWrite(IN3, HIGH); digitalWrite(IN4, LOW);
  analogWrite(ENA, 180);   analogWrite(ENB, 180);
}

void moveBackward() {
  digitalWrite(IN1, LOW);  digitalWrite(IN2, HIGH);
  digitalWrite(IN3, LOW);  digitalWrite(IN4, HIGH);
  analogWrite(ENA, 150);   analogWrite(ENB, 150);
}

void setup() {
  Serial.begin(115200);
  pinMode(IN1, OUTPUT); pinMode(IN2, OUTPUT);
  pinMode(IN3, OUTPUT); pinMode(IN4, OUTPUT);
  pinMode(ENA, OUTPUT); pinMode(ENB, OUTPUT);
  stopMotors();
  Serial.println("ESP32 Ready");
}

void loop() {
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();

    if      (cmd == "HAPPY")  moveForward();   // Robot approaches (happy)
    else if (cmd == "ANGRY")  moveBackward();  // Robot backs off  (angry)
    else if (cmd == "SAD")    stopMotors();    // Robot stays present (sad)
    else if (cmd == "STOP")   stopMotors();
  }
}
```

**Arduino IDE Board Settings:**
- Board: `ESP32 Dev Module`
- Upload Speed: `115200`
- Port: whichever COM port the ESP32 appears on

---

### Install pyserial on RPi

```bash
source /home/pi/MoodBot/.venv/bin/activate
pip install pyserial
```

---

### Wire ESP32 to RPi
Connect the ESP32 to the Raspberry Pi via a **USB-A to USB-C (or micro-USB)** cable.

On the RPi, find the serial port:
```bash
ls /dev/ttyUSB*
# Usually: /dev/ttyUSB0
```

---

### Add Serial Commands to `main.py`

Add this import at the top of `main.py`:
```python
import serial
```

Add this inside `main()` after `recognizer = create_recognizer()`:
```python
# Connect to ESP32 — change port to /dev/ttyUSB0 on Raspberry Pi
try:
    esp = serial.Serial('/dev/ttyUSB0', 115200, timeout=1)
    print("[INFO] ESP32 connected on /dev/ttyUSB0")
except Exception:
    esp = None
    print("[WARN] ESP32 not connected — motor control disabled")
```

Add this wherever `run_conversation()` is called (just before it):
```python
if esp:
    esp.write((emotion.upper() + '\n').encode())
```

---

## 4. Full Wiring Diagram

```
┌─────────────────────────────────────────────────────┐
│                  POWER                              │
│  4x 2.5V Motors Batteries (series) = ~10V          │
│  Battery (+) ──→ L298N VCC                         │
│  Battery (-) ──→ L298N GND ──→ ESP32 GND (common)  │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│              ESP32  ←→  L298N                       │
│  ESP32 GPIO14 (ENA) ──→ L298N ENA                  │
│  ESP32 GPIO26 (IN1) ──→ L298N IN1                  │
│  ESP32 GPIO27 (IN2) ──→ L298N IN2                  │
│  ESP32 GPIO12 (ENB) ──→ L298N ENB                  │
│  ESP32 GPIO25 (IN3) ──→ L298N IN3                  │
│  ESP32 GPIO33 (IN4) ──→ L298N IN4                  │
│  ESP32 GND          ──→ L298N GND                  │
│  ESP32 3.3V/5V      ──→ L298N 5V logic (optional)  │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│              L298N  ←→  Motors                      │
│  L298N OUT1 + OUT2 ──→ Left  motors (2x in parallel)│
│  L298N OUT3 + OUT4 ──→ Right motors (2x in parallel)│
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│         Raspberry Pi  ←→  ESP32                     │
│  RPi USB port ──→ ESP32 USB port (serial comms)     │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│         Raspberry Pi  ←→  Peripherals               │
│  RPi CSI port      ──→ Camera Module                │
│  RPi USB port      ──→ USB Microphone               │
│  RPi USB/3.5mm     ──→ Speaker                      │
└─────────────────────────────────────────────────────┘
```

---

## 5. Troubleshooting

| Problem | Fix |
|---|---|
| `Cannot open webcam` | Try `CAMERA_INDEX = 1` in `main.py` |
| `No speech detected` | Check microphone with `arecord -l` (Linux) or Sound Settings (Windows) |
| `Speech recognition error` | Check internet — Google API requires a connection |
| `ImportError: fer` | Use `from fer.fer import FER` (already fixed) |
| `ESP32 not found` | Run `ls /dev/ttyUSB*` on RPi — may be `/dev/ttyUSB1` |
| `L298N motors not moving` | Check ENA/ENB are HIGH or PWM > 0; verify battery polarity |
| `RPi too slow` | Lower `FRAME_WIDTH=320`, `FRAME_HEIGHT=240`, `ANALYSE_EVERY_N=20` |
| `TTS not working on RPi` | Run `sudo apt install espeak-ng` and reboot |

---

*Last updated: March 2026*
