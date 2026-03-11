"""
voice_io.py — Cross-platform Text-to-Speech and Speech-to-Text.

  TTS:
    Windows      → pyttsx3 (SAPI5).  A new engine instance is created per
                   call so it works safely from any background thread.
    Linux / Pi   → espeak-ng subprocess (no Python bindings needed).

  STT:
    Both platforms → Groq Whisper (whisper-large-v3-turbo).
    Audio is captured with SpeechRecognition, saved to a temp WAV file,
    uploaded to Groq, and the temp file is always cleaned up in a finally block.
    This avoids the flac.exe PermissionError that occurs on Windows when using
    SpeechRecognition's built-in Google engine.
"""

import os
import platform
import struct
import subprocess
import tempfile
import time

import speech_recognition as sr

IS_WINDOWS = platform.system() == "Windows"

# ── Whisper hallucination filter ──────────────────────────────────────────────
# Whisper commonly outputs these phrases when transcribing silence or noise.
# All comparisons are done in lowercase with punctuation stripped.
_HALLUCINATION_PHRASES = frozenset({
    "thank you",
    "thanks for watching",
    "thank you for watching",
    "subscribe",
    "like and subscribe",
    "please subscribe",
    "thank you for listening",
    "thanks for listening",
    "bye",
    "goodbye",
    "see you next time",
    "you",
    "the end",
    "so",
    "i'm sorry",
    "",
})

# ── Mic index cache ───────────────────────────────────────────────────────────
# Scanned once at first listen() call so we don't enumerate devices every turn.
_webcam_mic_index: int | None = None
_mic_scanned: bool = False


def _find_webcam_mic() -> int | None:
    """Scan microphone list once and return the index of the USB webcam's mic.

    Searches for names containing 'camera', 'webcam', 'usb', 'video', or 'cam'.
    Result is cached — the scan only runs on the very first call.
    Returns None if not found, causing sr.Microphone() to use the system default.
    """
    global _webcam_mic_index, _mic_scanned
    if _mic_scanned:                          # already scanned — return cached result
        return _webcam_mic_index

    keywords = ["camera", "webcam", "usb", "video", "cam"]
    try:
        names = sr.Microphone.list_microphone_names()
        print("[MIC] Scanning available microphones:")
        for i, name in enumerate(names):
            print(f"        [{i}] {name}")
        for i, name in enumerate(names):
            if any(kw in name.lower() for kw in keywords):
                print(f"[MIC] ✓ Auto-selected USB webcam mic  → [{i}] {name}")
                _webcam_mic_index = i
                break
        if _webcam_mic_index is None:
            print("[MIC] No USB webcam mic found — using system default")
    except Exception as e:
        print(f"[MIC] Could not enumerate microphones: {e}")

    _mic_scanned = True
    return _webcam_mic_index


def _is_hallucination(text: str) -> bool:
    """Return True if the transcribed text looks like a Whisper hallucination."""
    import re
    cleaned = re.sub(r"[^\w\s]", "", text.lower()).strip()
    if cleaned in _HALLUCINATION_PHRASES:
        return True
    # Very short single-word outputs are usually noise artefacts
    if len(cleaned.split()) <= 1 and len(cleaned) < 4:
        return True
    return False


def _audio_rms(wav_data: bytes) -> float:
    """Compute RMS energy of raw PCM16 audio (skip 44-byte WAV header)."""
    pcm = wav_data[44:]  # skip WAV header
    if len(pcm) < 2:
        return 0.0
    n_samples = len(pcm) // 2
    samples = struct.unpack(f"<{n_samples}h", pcm[:n_samples * 2])
    rms = (sum(s * s for s in samples) / n_samples) ** 0.5
    return rms


def speak(text: str, rate: int = 145) -> None:
    """
    Speak text synchronously and print it to the terminal.
    Safe to call from any thread on both Windows and Linux/Pi.
    """
    print(f"\n[BOT REPLY] {text}")
    print("-" * 60)
    if IS_WINDOWS:
        import pyttsx3
        # Create a fresh engine instance — required for thread safety on Windows
        engine = pyttsx3.init()
        engine.setProperty("rate", rate)
        engine.say(text)
        engine.runAndWait()
        engine.stop()
    else:
        # en+f3 = English female voice; -s = speed (words/min)
        subprocess.run(
            ["espeak-ng", "-s", str(rate), "-v", "en+f3", text],
            check=False,
        )
    # Brief pause after TTS so speaker audio dissipates before mic opens
    time.sleep(0.6)


def listen(groq_client, whisper_model: str,
           timeout: int = 5, phrase_limit: int = 8) -> str:
    """
    Record one utterance from the default microphone and transcribe
    it via Groq Whisper.

    Returns the transcribed text, or an empty string on timeout / error.
    Filters out Whisper hallucinations and low-energy (silent) audio.
    """
    recognizer = sr.Recognizer()
    recognizer.dynamic_energy_threshold = True
    recognizer.energy_threshold = 150

    # ── Step 1: capture microphone audio ─────────────────────────────────────
    mic_index = None if IS_WINDOWS else _find_webcam_mic()
    try:
        with sr.Microphone(device_index=mic_index) as source:
            print("\n[LISTENING] Adjusting for ambient noise…")
            recognizer.adjust_for_ambient_noise(source, duration=1.0)
            print(f"[LISTENING] *** Speak now  (up to {phrase_limit}s) ***")
            audio = recognizer.listen(
                source,
                timeout=timeout,
                phrase_time_limit=phrase_limit,
            )
        print("[LISTENING] Audio captured — checking quality…")
    except sr.WaitTimeoutError:
        print("[LISTENING] Timed out — no speech detected.")
        return ""

    # ── Step 1b: energy gate — skip transcription if audio is near-silent ────
    wav_data = audio.get_wav_data()
    rms = _audio_rms(wav_data)
    if rms < 80:
        print(f"[LISTENING] Audio too quiet (RMS={rms:.0f}) — skipping transcription.")
        return ""

    # ── Step 2: transcribe via Groq Whisper ───────────────────────────────────
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(wav_data)
            tmp_path = tmp.name

        with open(tmp_path, "rb") as f:
            result = groq_client.audio.transcriptions.create(
                model=whisper_model,
                file=("audio.wav", f, "audio/wav"),
            )

        text = result.text.strip()

        # ── Step 3: filter Whisper hallucinations ─────────────────────────────
        if _is_hallucination(text):
            print(f"[LISTENING] Filtered hallucination: \"{text}\"")
            return ""

        print(f"[YOU SAID]  \"{text}\"")
        return text

    except Exception as exc:
        print(f"[STT ERROR] {exc}")
        return ""

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
