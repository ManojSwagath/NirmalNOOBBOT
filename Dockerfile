# ============================================================
#  MoodBot — Docker image (multi-arch: amd64 + arm64/Pi)
#  Provides camera, microphone, display, and speaker access
#  for real-time emotion detection + AI companion.
#
#  Build:   docker build -t moodbot .
#  Run:     docker-compose up   (recommended)
# ============================================================

FROM python:3.11-slim

# Prevent interactive prompts during apt install
ENV DEBIAN_FRONTEND=noninteractive

# System dependencies for OpenCV, PyAudio, espeak (TTS), GTK (imshow), audio, fonts
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ python3-dev \
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 \
    libgtk-3-0 \
    portaudio19-dev \
    espeak-ng libespeak-ng1 \
    libasound2 libpulse0 \
    alsa-utils libasound2-plugins \
    fonts-dejavu-core \
    v4l-utils \
    liblapack-dev libblas-dev libhdf5-dev \
    libjpeg-dev libpng-dev libtiff-dev \
    && rm -rf /var/lib/apt/lists/*

# Default PulseAudio ALSA routing (host PulseAudio is mounted at runtime)
RUN echo 'pcm.!default { type pulse }' > /etc/asound.conf \
 && echo 'ctl.!default { type pulse }' >> /etc/asound.conf

# Default camera index — override with CAMERA_INDEX env var at runtime
ENV CAMERA_INDEX=0

WORKDIR /app

# Install Python deps first (layer caching)
# Uses Pi-compatible requirements (works on all platforms)
COPY requirements-pi.txt .
RUN pip install --no-cache-dir -r requirements-pi.txt

# Install mediapipe on amd64 (not available on arm64/Pi)
# Gives dual-signal emotion detection (FER + face geometry fusion)
RUN if [ "$(dpkg --print-architecture)" = "amd64" ]; then \
        pip install --no-cache-dir mediapipe>=0.10.0; \
    fi

# Copy application code
COPY src/ ./src/
COPY main.py ./

# Create data directory for persistent memory
RUN mkdir -p data

# Copy model file if it exists (optional — downloaded at runtime otherwise)
COPY face_landmarker.tas[k] ./

CMD ["python", "main.py"]
