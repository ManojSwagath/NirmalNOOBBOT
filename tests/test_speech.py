"""
=============================================================================
  Speech Conversation Test  —  5 exchanges
=============================================================================
  Fixes the PermissionError (flac.exe) by sending raw WAV audio directly
  to Groq Whisper instead of using the Google Speech Recognition backend.

  Run:
      python test_speech.py
=============================================================================
"""

import os
import tempfile

import win32com.client as wincl
import speech_recognition as sr
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

# ── Config ────────────────────────────────────────────────────
CHAT_MODEL     = "llama-3.1-8b-instant"
WHISPER_MODEL  = "whisper-large-v3-turbo"
TTS_RATE       = 155
EXCHANGES      = 5
LISTEN_TIMEOUT = 6       # seconds to wait for the user to start speaking
PHRASE_LIMIT   = 10      # max seconds per phrase


# ── TTS (Windows SAPI5 via win32com — reliable from any terminal) ───────────
def create_tts():
    sapi = wincl.Dispatch("SAPI.SpVoice")
    sapi.Rate = -1   # -10 (slow) … 10 (fast); default 0
    voices = sapi.GetVoices()
    for i in range(voices.Count):
        v = voices.Item(i)
        desc = v.GetDescription()
        if "zira" in desc.lower():
            sapi.Voice = v
            print(f"[TTS]  Voice  : {desc}")
            return sapi
    print("[TTS]  Zira not found — using default voice.")
    return sapi


def speak(text: str, sapi):
    print(f"[AI]   {text}")
    sapi.Speak(text)   # synchronous — blocks until audio finishes


# ── STT via Groq Whisper (no flac.exe needed) ─────────────────
def listen_and_transcribe(recognizer: sr.Recognizer, client: Groq) -> str | None:
    """
    Capture microphone audio → save as WAV → transcribe with Groq Whisper.
    Avoids the SpeechRecognition Google backend that spawns flac.exe.
    """
    with sr.Microphone() as source:
        print("[MIC]  Listening … (speak now)")
        recognizer.adjust_for_ambient_noise(source, duration=0.3)
        try:
            audio = recognizer.listen(
                source,
                timeout=LISTEN_TIMEOUT,
                phrase_time_limit=PHRASE_LIMIT,
            )
        except sr.WaitTimeoutError:
            print("[MIC]  No speech detected (timeout).")
            return None

    # Write raw PCM as a proper WAV file and send it to Groq Whisper
    wav_bytes = audio.get_wav_data()
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(wav_bytes)
            tmp_path = tmp.name

        print("[STT]  Transcribing with Groq Whisper …")
        with open(tmp_path, "rb") as f:
            result = client.audio.transcriptions.create(
                model=WHISPER_MODEL,
                file=("audio.wav", f, "audio/wav"),
            )
        text = result.text.strip()
        if text:
            print(f"[YOU]  {text}")
            return text
        return None

    except Exception as exc:
        print(f"[STT]  Error: {exc}")
        return None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ── Groq chat reply ────────────────────────────────────────────
def get_reply(client: Groq, history: list) -> str:
    try:
        resp = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=history,
            max_tokens=120,
            temperature=0.75,
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        print(f"[GROQ] Error: {exc}")
        return "I'm having a little trouble, but I'm still here for you!"


# ── Main ──────────────────────────────────────────────────────
def main():
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        print("[ERROR] GROQ_API_KEY not set in .env")
        return

    client     = Groq(api_key=api_key)
    sapi       = create_tts()
    recognizer = sr.Recognizer()
    recognizer.dynamic_energy_threshold = True

    history = [
        {
            "role": "system",
            "content": (
                "You are a warm, empathetic AI companion. "
                "Keep each response concise (1-2 sentences) and emotionally supportive. "
                "End every reply with a caring follow-up question."
            ),
        }
    ]

    print("\n" + "=" * 52)
    print("  SPEECH CONVERSATION TEST  —  5 exchanges")
    print("=" * 52 + "\n")

    greeting = "Hello! I'm your AI companion. How are you feeling today?"
    history.append({"role": "assistant", "content": greeting})
    speak(greeting, sapi)

    for i in range(1, EXCHANGES + 1):
        print(f"\n── Exchange {i} / {EXCHANGES} ──────────────────────")

        user_text = listen_and_transcribe(recognizer, client)

        if not user_text:
            speak("It seems like you're quiet. I'm here whenever you're ready!", sapi)
            break

        history.append({"role": "user", "content": user_text})
        reply = get_reply(client, history)
        history.append({"role": "assistant", "content": reply})
        speak(reply, sapi)

    print("\n── Test complete ─────────────────────────────────────")
    speak("It was great talking with you! Take care!", sapi)


if __name__ == "__main__":
    main()
