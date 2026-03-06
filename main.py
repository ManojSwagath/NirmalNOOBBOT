"""
=============================================================================
  Empathetic AI Companion  —  Facial Emotion Detection & Spoken Response
=============================================================================

  This program uses a webcam to detect a person's face, analyse their
  dominant emotion with a pretrained CNN (FER library), and respond with
  a spoken empathetic message via pyttsx3.

  Runs on:
    • Windows / macOS / Linux  (local PC testing)
    • Raspberry Pi 5 (4 GB)    (deployment target)

  Quick start:
    pip install -r requirements.txt
    python main.py

  Raspberry Pi prerequisites:
    sudo apt update
    sudo apt install espeak-ng libespeak1 libatlas-base-dev libhdf5-dev
    pip install -r requirements.txt

  Press 'q' in the webcam window to quit.
=============================================================================
"""

import os
import platform
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque

from dotenv import load_dotenv
load_dotenv()   # reads .env into os.environ before anything else

import cv2
import speech_recognition as sr
from fer.fer import FER
from groq import Groq

IS_WINDOWS = platform.system() == "Windows"
if IS_WINDOWS:
    import win32com.client as wincl

# ──────────────────────────────────────────────────────────────
# 1.  CONFIGURATION  — tweak these constants freely
# ──────────────────────────────────────────────────────────────

CAMERA_INDEX      = 0          # 0 = default webcam
FRAME_WIDTH       = 640        # Lower to 320 on Raspberry Pi for speed
FRAME_HEIGHT      = 480        # Lower to 240 on Raspberry Pi for speed
ANALYSE_EVERY_N   = 5          # Run emotion model every Nth frame (saves CPU)
STABLE_COUNT      = 2          # Require N consecutive same-emotion reads before speaking
MIN_CONFIDENCE    = 0.50       # Ignore detections below this confidence (0–1)
TTS_RATE          = 160        # Words-per-minute for the speech engine
WINDOW_NAME       = "Empathetic AI Companion"
CONVERSATION_LIMIT = 5         # Max back-and-forth exchanges per emotion session
LISTEN_TIMEOUT    = 5          # Seconds to wait for speech
PHRASE_TIME_LIMIT = 8          # Max seconds of a single phrase
GROQ_MODEL        = "llama-3.1-8b-instant"   # Groq's fastest model (~300 ms latency)
WHISPER_MODEL     = "whisper-large-v3-turbo"  # Groq Whisper for speech-to-text

# ──────────────────────────────────────────────────────────────
# 2.  EMOTION → RESPONSE TEMPLATES
# ──────────────────────────────────────────────────────────────

EMOTION_RESPONSES = {
    "happy":    "Hi! You look very happy today! Keep smiling!",
    "sad":      "Hello… You look a little sad. Is everything okay? I'm here for you.",
    "angry":    "Hey, try to relax. Take a deep breath. Everything will be alright.",
}

# Colour per emotion for the on-screen label  (BGR format)
EMOTION_COLOURS = {
    "happy":    (0, 255, 0),     # green
    "sad":      (255, 0, 0),     # blue
    "angry":    (0, 0, 255),     # red
}

# Only track these three emotions
TRACKED_EMOTIONS = set(EMOTION_RESPONSES.keys())

# System prompt template sent to Groq for each emotion session
GROQ_SYSTEM_PROMPT = (
    "You are a warm, empathetic AI companion. "
    "The person in front of you appears to be feeling {emotion}. "
    "Keep every response concise (1-2 sentences) and emotionally supportive. "
    "End each reply with a caring follow-up question to keep the conversation going. "
    "Do not repeat the emotion word excessively — just be naturally caring."
)


# ──────────────────────────────────────────────────────────────
# 3.  GROQ AI CLIENT
# ──────────────────────────────────────────────────────────────

def create_groq_client() -> Groq:
    """
    Initialise the Groq API client.
    Reads GROQ_API_KEY from the environment (set it before running):
        Windows:  $env:GROQ_API_KEY = "gsk_..."
        Linux:    export GROQ_API_KEY="gsk_..."
    Get a free key at https://console.groq.com
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        print("[ERROR] GROQ_API_KEY environment variable is not set.")
        print("        Get a free key at https://console.groq.com")
        print("        Then run:  $env:GROQ_API_KEY = 'gsk_...'")
        sys.exit(1)
    return Groq(api_key=api_key)


def get_groq_reply(client: Groq, history: list) -> str:
    """Send the conversation history to Groq and return the reply text."""
    try:
        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=history,
            max_tokens=120,
            temperature=0.75,
        )
        return completion.choices[0].message.content.strip()
    except Exception as exc:
        print(f"[WARN] Groq API error: {exc}")
        return "I'm having a little trouble right now, but I'm still here for you."


# ──────────────────────────────────────────────────────────────
# 4.  EMOTION DETECTION  (FER — pretrained Keras CNN)
# ──────────────────────────────────────────────────────────────

def create_detector() -> FER:  # (section numbering shifted — Groq is now section 3)
    """
    Initialise the FER emotion detector.

    Uses OpenCV's Haar-cascade face detector under the hood
    (mtcnn=False) which is much lighter and faster — ideal for
    real-time webcam and Raspberry Pi deployment.
    """
    print("[INFO] Loading FER emotion detection model …")
    detector = FER(mtcnn=False)   # mtcnn=True is more accurate but slower
    print("[INFO] Model loaded successfully.")
    return detector


def detect_emotion(frame, detector: FER) -> tuple:
    """
    Analyse a single BGR frame and return:
        (dominant_emotion: str | None,
         bounding_box:     tuple(x,y,w,h) | None,
         all_scores:       dict | None)

    Returns (None, None, None) when no face is detected.
    """
    try:
        results = detector.detect_emotions(frame)

        if not results:
            return None, None, None

        # Take the face with the highest detection confidence
        top = max(results, key=lambda r: max(r["emotions"].values()))
        box      = top["box"]                # (x, y, w, h)
        emotions = top["emotions"]           # {'happy': 0.92, 'sad': 0.01, …}
        dominant = max(emotions, key=emotions.get)

        return dominant, box, emotions

    except Exception as exc:
        print(f"[WARN] Emotion detection error: {exc}")
        return None, None, None


# ──────────────────────────────────────────────────────────────
# 4.  TEXT-TO-SPEECH  (SAPI5 on Windows • espeak-ng on Linux/Pi)
# ──────────────────────────────────────────────────────────────

def create_tts_engine():
    """
    Windows : SAPI5 via win32com  (Zira female voice, reliable from threads)
    Linux/Pi: returns None — espeak-ng is called directly in speak_text()
    """
    if IS_WINDOWS:
        sapi = wincl.Dispatch("SAPI.SpVoice")
        sapi.Rate = -1
        voices = sapi.GetVoices()
        for i in range(voices.Count):
            v = voices.Item(i)
            desc = v.GetDescription()
            if "zira" in desc.lower():
                sapi.Voice = v
                print(f"[TTS]  Using voice: {desc}")
                return sapi
        print("[TTS]  Zira not found — using default Windows voice.")
        return sapi
    else:
        # Linux / Raspberry Pi — verify espeak-ng is installed
        result = subprocess.run(["which", "espeak-ng"], capture_output=True)
        if result.returncode != 0:
            print("[TTS]  espeak-ng not found. Run: sudo apt install espeak-ng")
        else:
            print("[TTS]  Using espeak-ng (en+f3 — female voice)")
        return None  # Linux TTS is stateless — no engine object needed


def speak_text(text: str, tts):
    """Speak text synchronously regardless of platform."""
    try:
        if IS_WINDOWS:
            tts.Speak(text)
        else:
            # espeak-ng: -s speed, -v voice (en+f3 = English female)
            subprocess.run(
                ["espeak-ng", "-s", "145", "-v", "en+f3", text],
                check=True,
            )
    except Exception as exc:
        print(f"[WARN] TTS error: {exc}")




# ──────────────────────────────────────────────────────────────
# 4b. SPEECH RECOGNITION  (Groq Whisper — no flac.exe subprocess)
# ──────────────────────────────────────────────────────────────

def create_recognizer() -> sr.Recognizer:
    """Create and configure a speech recognizer."""
    recognizer = sr.Recognizer()
    recognizer.dynamic_energy_threshold = True
    return recognizer


def listen_for_speech(recognizer: sr.Recognizer, groq_client: Groq) -> str | None:
    """
    Capture microphone audio and transcribe via Groq Whisper (WAV upload).
    Avoids the flac.exe subprocess that causes PermissionError on Windows.
    """
    try:
        with sr.Microphone() as source:
            print("[LISTEN] Listening … (speak now)")
            recognizer.adjust_for_ambient_noise(source, duration=0.3)
            audio = recognizer.listen(
                source, timeout=LISTEN_TIMEOUT, phrase_time_limit=PHRASE_TIME_LIMIT
            )
    except sr.WaitTimeoutError:
        print("[LISTEN] No speech detected (timeout).")
        return None

    tmp_path = None
    try:
        wav_bytes = audio.get_wav_data()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(wav_bytes)
            tmp_path = tmp.name

        print("[LISTEN] Transcribing with Groq Whisper …")
        with open(tmp_path, "rb") as f:
            result = groq_client.audio.transcriptions.create(
                model="whisper-large-v3-turbo",
                file=("audio.wav", f, "audio/wav"),
            )
        text = result.text.strip()
        if text:
            print(f"[HEARD]  \"{text}\"")
            return text
        return None

    except Exception as exc:
        print(f"[WARN] STT error: {exc}")
        return None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ──────────────────────────────────────────────────────────────
# 4c. CONVERSATION ENGINE  (powered by Groq)
# ──────────────────────────────────────────────────────────────

def run_conversation(emotion: str, sapi,
                     recognizer: sr.Recognizer, spoken_flag: dict,
                     groq_client: Groq):
    """
    Run a back-and-forth voice conversation in a background thread.

    Flow:
      1. Speak the fixed emotion greeting immediately (no API latency).
      2. After each user reply, send the full chat history to Groq
         (llama-3.1-8b-instant, ~300 ms) and speak the contextual reply.
      3. Repeat up to CONVERSATION_LIMIT exchanges.
    """
    def _converse():
        # Windows only: COM must be initialised per-thread (SAPI is STA-based)
        if IS_WINDOWS:
            import pythoncom
            pythoncom.CoInitialize()
            thread_tts = wincl.Dispatch("SAPI.SpVoice")
            thread_tts.Rate = -1
            voices = thread_tts.GetVoices()
            for i in range(voices.Count):
                v = voices.Item(i)
                if "zira" in v.GetDescription().lower():
                    thread_tts.Voice = v
                    break
        else:
            thread_tts = None  # Linux: speak_text() calls espeak-ng directly

        spoken_flag["busy"] = True
        try:
            # Build the conversation history with a system prompt
            history = [
                {
                    "role": "system",
                    "content": GROQ_SYSTEM_PROMPT.format(emotion=emotion),
                }
            ]

            # Speak the instant local greeting (zero API latency)
            greeting = EMOTION_RESPONSES.get(emotion, "Hello! How are you?")
            history.append({"role": "assistant", "content": greeting})
            print(f'[SPEAK]   "{greeting}"')
            speak_text(greeting, thread_tts)

            msg_count = 0

            while msg_count < CONVERSATION_LIMIT:
                # Listen for user reply
                user_text = listen_for_speech(recognizer, groq_client)

                if user_text is None:
                    farewell = "I'm here whenever you're ready to talk. Take care!"
                    print(f'[SPEAK]   "{farewell}"')
                    speak_text(farewell, thread_tts)
                    break

                msg_count += 1
                history.append({"role": "user", "content": user_text})
                print(f'[GROQ]    Fetching reply for: "{user_text}"')

                # Get a contextual, dynamic reply from Groq
                reply = get_groq_reply(groq_client, history)
                history.append({"role": "assistant", "content": reply})
                print(f'[SPEAK]   "{reply}"')
                speak_text(reply, thread_tts)

                if msg_count >= CONVERSATION_LIMIT:
                    closing = "It was really nice talking with you! Take care and stay strong!"
                    print(f'[SPEAK]   "{closing}"')
                    speak_text(closing, thread_tts)
                    break

        finally:
            spoken_flag["busy"] = False
            if IS_WINDOWS:
                import pythoncom
                pythoncom.CoUninitialize()
            print("[CONVO] Conversation ended.")

    thread = threading.Thread(target=_converse, daemon=True)
    thread.start()


# ──────────────────────────────────────────────────────────────
# 5.  CAMERA HELPER  (cross-platform: Windows + Raspberry Pi)
# ──────────────────────────────────────────────────────────────

def open_camera(index: int = CAMERA_INDEX) -> cv2.VideoCapture:
    """
    Try multiple backends so the same code works on both
    Windows (DirectShow) and Raspberry Pi (V4L2).
    """
    backends = [
        ("DirectShow", cv2.CAP_DSHOW),
        ("V4L2",       cv2.CAP_V4L2),
        ("Any",        cv2.CAP_ANY),
    ]
    for name, backend in backends:
        cap = cv2.VideoCapture(index, backend)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,  FRAME_WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
            cap.set(cv2.CAP_PROP_FOURCC,
                    cv2.VideoWriter_fourcc(*"MJPG"))
            print(f"[INFO] Camera opened with {name} backend.")
            return cap
    return None


# ──────────────────────────────────────────────────────────────
# 6.  DRAW OVERLAY  — bounding box + emotion label on frame
# ──────────────────────────────────────────────────────────────

def draw_overlay(frame, emotion: str, box: tuple, scores: dict):
    """
    Draw a rectangle around the detected face and label it with
    the dominant emotion and its confidence score.
    """
    x, y, w, h = box
    colour = EMOTION_COLOURS.get(emotion, (255, 255, 255))

    # Bounding box
    cv2.rectangle(frame, (x, y), (x + w, y + h), colour, 2)

    # Label background
    confidence = scores.get(emotion, 0.0)
    label = f"{emotion.upper()} ({confidence:.0%})"
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)
    cv2.rectangle(frame, (x, y - th - 14), (x + tw + 6, y), colour, -1)

    # Label text
    cv2.putText(frame, label, (x + 3, y - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2, cv2.LINE_AA)

    # Bottom-left instructions
    cv2.putText(frame, "Press 'q' to quit", (10, frame.shape[0] - 15),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1, cv2.LINE_AA)


# ──────────────────────────────────────────────────────────────
# 7.  MAIN LOOP
# ──────────────────────────────────────────────────────────────

def main():
    """
    Core camera loop:
      1. Capture a frame from the webcam.
      2. Every N frames, run the FER emotion detector.
      3. Overlay the detected emotion on the video feed.
      4. If the emotion is stable for STABLE_COUNT consecutive
         reads *and* differs from the last spoken emotion,
         speak the empathetic response in a background thread.
      5. Repeat until the user presses 'q'.
    """

    # --- Initialise components ---
    cap = open_camera()
    if cap is None:
        print("[ERROR] Cannot open webcam. Check camera connection.")
        sys.exit(1)

    detector    = create_detector()
    tts_sapi    = create_tts_engine()
    recognizer  = create_recognizer()
    groq_client = create_groq_client()

    # --- State variables ---
    frame_count    = 0              # Total frames captured
    current_emotion = None          # Latest detected emotion
    current_box     = None          # Latest bounding box
    current_scores  = None          # Latest emotion scores dict
    last_spoken     = None          # Last emotion that was spoken aloud
    emotion_buffer  = deque(maxlen=STABLE_COUNT)  # Rolling window for stability
    spoken_flag     = {"busy": False}             # Shared flag — is TTS speaking?

    print("\n" + "=" * 55)
    print("  EMPATHETIC AI COMPANION  —  Running")
    print("  Press 'q' in the video window to quit.")
    print("=" * 55 + "\n")

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("[WARN] Failed to grab frame. Retrying …")
                time.sleep(0.1)
                continue

            frame_count += 1

            # --- Run emotion detection every N frames ---
            if frame_count % ANALYSE_EVERY_N == 0:
                emotion, box, scores = detect_emotion(frame, detector)

                if emotion is not None:
                    # Skip weak detections and untracked emotions
                    if emotion not in TRACKED_EMOTIONS:
                        continue
                    if scores[emotion] < MIN_CONFIDENCE:
                        continue
                    current_emotion = emotion
                    current_box     = box
                    current_scores  = scores
                    emotion_buffer.append(emotion)

                    # Console output
                    print(f"[EMOTION] {emotion.upper():>10s}  "
                          f"(confidence {scores[emotion]:.0%})")

                    # --- Speak & start conversation when emotion is STABLE and NEW ---
                    all_same = (len(emotion_buffer) == STABLE_COUNT
                                and len(set(emotion_buffer)) == 1)

                    if (all_same
                            and emotion != last_spoken
                            and not spoken_flag["busy"]):
                        run_conversation(emotion, tts_sapi, recognizer, spoken_flag, groq_client)
                        last_spoken = emotion

            # --- Draw overlay if we have a detection ---
            if current_emotion and current_box is not None:
                draw_overlay(frame, current_emotion, current_box, current_scores)

            # --- Show the video feed ---
            cv2.imshow(WINDOW_NAME, frame)

            # --- Quit on 'q' key ---
            if cv2.waitKey(1) & 0xFF == ord("q"):
                print("\n[INFO] Quitting …")
                break

    except KeyboardInterrupt:
        print("\n[INFO] Interrupted by user.")

    finally:
        # --- Clean up ---
        cap.release()
        cv2.destroyAllWindows()
        print("[INFO] Camera released. Goodbye!")


# ──────────────────────────────────────────────────────────────
# 8.  ENTRY POINT
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    main()
