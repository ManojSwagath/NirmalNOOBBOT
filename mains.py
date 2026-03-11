import cv2
import numpy as np
import urllib.request
import onnxruntime as ort
import speech_recognition as sr
from groq import Groq
from collections import deque
import tempfile
import os
import re
import struct
import time
import threading
import pyttsx3
from dotenv import load_dotenv

load_dotenv()

# -----------------------------
# CONFIGURATION
# -----------------------------

FRAME_WIDTH = 320
FRAME_HEIGHT = 240

MIN_CONFIDENCE = 0.15   # threshold on the COMBINED group score
STABLE_FRAMES = 6

EMOTION_BUFFER_SIZE = 10
EMOTION_HISTORY_SIZE = 50

TRACKED_EMOTIONS = ["happy", "sad", "angry"]

# Maps FER's 7 raw emotions → your 3 target emotions.
# Multiple FER expressions that look like the same feeling are grouped together.
EMOTION_GROUPS = {
    "happy": ["happy", "surprise"],          # smiling, excited, open-mouth joy
    "sad":   ["sad",   "fear"],               # crying, worried, scared, downturned
    "angry": ["angry", "disgust", "contempt"], # frowning, frustrated, tight-lipped
}

EMOTION_COLOURS = {
    "happy": (0, 255, 0),
    "sad":   (255, 0, 0),
    "angry": (0, 0, 255),
}

# -----------------------------
# EMOTION GROUPING
# Combines raw FER scores so different facial variations
# (e.g. scared / worried → sad) all trigger the right emotion.
# -----------------------------

def map_emotion(raw_scores: dict) -> tuple:
    """
    Fold FER's 8 raw scores into our 3 target groups.
    When 'neutral' dominates (>40%), suppress all group scores.
    Returns (target_emotion, combined_confidence) or (None, 0) if
    no group reaches MIN_CONFIDENCE.
    """
    neutral_score = raw_scores.get("neutral", 0.0)
    group_scores = {}
    for target, members in EMOTION_GROUPS.items():
        group_scores[target] = sum(raw_scores.get(m, 0.0) for m in members)

    # Only suppress when face is strongly neutral (>60%)
    if neutral_score > 0.6:
        suppression = max(0.3, 1.0 - neutral_score)
        group_scores = {e: s * suppression for e, s in group_scores.items()}

    best = max(group_scores, key=group_scores.get)
    return best, group_scores[best]


# -----------------------------
# GROQ CLIENT
# -----------------------------

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# -----------------------------
# TEXT TO SPEECH
# Each call creates its own engine so it works safely from any thread
# -----------------------------

# ── Whisper hallucination filter ──────────────────────────────────────────────
_HALLUCINATION_PHRASES = frozenset({
    "thank you", "thanks for watching", "thank you for watching",
    "subscribe", "like and subscribe", "please subscribe",
    "thank you for listening", "thanks for listening",
    "bye", "goodbye", "see you next time", "you", "the end", "so",
    "i'm sorry", "",
})

def _is_hallucination(text: str) -> bool:
    cleaned = re.sub(r"[^\w\s]", "", text.lower()).strip()
    if cleaned in _HALLUCINATION_PHRASES:
        return True
    if len(cleaned.split()) <= 1 and len(cleaned) < 4:
        return True
    return False

def _audio_rms(wav_data: bytes) -> float:
    pcm = wav_data[44:]
    if len(pcm) < 2:
        return 0.0
    n_samples = len(pcm) // 2
    samples = struct.unpack(f"<{n_samples}h", pcm[:n_samples * 2])
    return (sum(s * s for s in samples) / n_samples) ** 0.5

def speak(text):
    print("AI:", text)
    engine = pyttsx3.init()
    engine.setProperty("rate", 145)
    engine.say(text)
    engine.runAndWait()
    engine.stop()
    time.sleep(0.6)

# -----------------------------
# SPEECH RECOGNITION
# -----------------------------

recognizer = sr.Recognizer()

def listen():

    recognizer.energy_threshold = 300

    with sr.Microphone() as source:

        print("Listening...")
        recognizer.adjust_for_ambient_noise(source, duration=1.0)

        try:
            audio = recognizer.listen(source, timeout=5, phrase_time_limit=8)
        except sr.WaitTimeoutError:
            print("No speech detected.")
            return ""

    wav_data = audio.get_wav_data()
    rms = _audio_rms(wav_data)
    if rms < 200:
        print(f"Audio too quiet (RMS={rms:.0f}) — skipping.")
        return ""

    with tempfile.NamedTemporaryFile(suffix=".wav",delete=False) as tmp:
        tmp.write(wav_data)
        path = tmp.name

    try:
        with open(path, "rb") as f:
            result = groq_client.audio.transcriptions.create(
                model="whisper-large-v3-turbo",
                file=("speech.wav", f, "audio/wav")
            )
        text = result.text.strip()
        if _is_hallucination(text):
            print(f"Filtered hallucination: \"{text}\"")
            return ""
        print("User:", text)
        return text
    except Exception as exc:
        print("STT error:", exc)
        return ""
    finally:
        if os.path.exists(path):
            os.remove(path)

# -----------------------------
# AI REPLY
# -----------------------------

def ai_reply(user_text,emotion_context):

    messages = [
        {
            "role":"system",
            "content":f"You are a caring AI companion. The user currently feels {emotion_context}. Respond with empathy."
        },
        {
            "role":"user",
            "content":user_text
        }
    ]

    completion = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=messages
    )

    return completion.choices[0].message.content

# -----------------------------
# EMOTION MEMORY
# -----------------------------

emotion_buffer = deque(maxlen=EMOTION_BUFFER_SIZE)
emotion_history = deque(maxlen=EMOTION_HISTORY_SIZE)

# -----------------------------
# CONVERSATION  (runs in background thread so camera never freezes)
# -----------------------------

def run_conversation(emotion, hist_snapshot):
    greetings = {
        "sad":   "You look sad. I'm here if you want to talk.",
        "angry": "You seem angry. Take a deep breath.",
        "happy": "You look happy today!",
    }
    speak(greetings.get(emotion, "How are you feeling?"))

    if hist_snapshot.count("sad") > 20:
        speak("You seem sad for a long time. Do you want to talk about it?")
    if hist_snapshot.count("angry") > 15:
        speak("You seem frustrated. Maybe talking could help.")

    user_text = listen()
    if user_text:
        reply = ai_reply(user_text, emotion)
        speak(reply)


# -----------------------------
# EMOTION MODEL (ONNX — no TensorFlow needed)
# -----------------------------

_ONNX_MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src", "emotion-ferplus-8.onnx")
_ONNX_MODEL_URL = (
    "https://github.com/onnx/models/raw/main/"
    "validated/vision/body_analysis/emotion_ferplus/model/emotion-ferplus-8.onnx"
)
_FERPLUS_LABELS = ("neutral", "happy", "surprise", "sad",
                   "angry", "disgust", "fear", "contempt")

_YUNET_MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src", "face_detection_yunet_2023mar.onnx")
_YUNET_MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx"
)

if not os.path.exists(_ONNX_MODEL_PATH):
    os.makedirs(os.path.dirname(_ONNX_MODEL_PATH), exist_ok=True)
    print("Downloading emotion model (~34 MB)\u2026")
    urllib.request.urlretrieve(_ONNX_MODEL_URL, _ONNX_MODEL_PATH)

if not os.path.exists(_YUNET_MODEL_PATH):
    print("Downloading YuNet face detector (~300 KB)\u2026")
    urllib.request.urlretrieve(_YUNET_MODEL_URL, _YUNET_MODEL_PATH)

_ort_session = ort.InferenceSession(_ONNX_MODEL_PATH, providers=["CPUExecutionProvider"])
_ort_input = _ort_session.get_inputs()[0].name
_face_detector = cv2.FaceDetectorYN.create(_YUNET_MODEL_PATH, "", (0, 0), 0.6, 0.3)
_clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))


def detect_emotions(frame):
    """YuNet face detection + CLAHE preprocessing + ONNX emotion classification."""
    h_frame, w_frame = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    _face_detector.setInputSize((w_frame, h_frame))
    _, raw_faces = _face_detector.detect(frame)
    if raw_faces is None:
        return []

    results = []
    for face in raw_faces:
        x, y, w, h = int(face[0]), int(face[1]), int(face[2]), int(face[3])

        # Add 20% padding around face for better emotion recognition
        pad_w, pad_h = int(w * 0.2), int(h * 0.2)
        x1 = max(0, x - pad_w)
        y1 = max(0, y - pad_h)
        x2 = min(w_frame, x + w + pad_w)
        y2 = min(h_frame, y + h + pad_h)

        roi = gray[y1:y2, x1:x2]
        if roi.size == 0:
            continue

        roi = _clahe.apply(roi)
        roi = cv2.resize(roi, (64, 64)).astype(np.float32)
        logits = _ort_session.run(None, {_ort_input: roi.reshape(1, 1, 64, 64)})[0][0]
        probs = np.exp(logits - logits.max())
        probs /= probs.sum()
        emotions = {_FERPLUS_LABELS[i]: float(probs[i]) for i in range(len(_FERPLUS_LABELS))}
        results.append({"box": [x, y, w, h], "emotions": emotions})
    return results


# -----------------------------
# CAMERA
# -----------------------------

cap = cv2.VideoCapture(0)
cap.set(3, FRAME_WIDTH)
cap.set(4, FRAME_HEIGHT)

print("AI Companion Started")

last_emotion      = None
emotion_start_time = None
conversation_active = False   # True while background thread is running

# Last known face state — updated each detection, drawn every frame
display_emotion    = None
display_box        = None
display_confidence = 0.0

# -----------------------------
# MAIN LOOP
# -----------------------------

while True:

    ret, frame = cap.read()
    if not ret:
        break

    # --- Emotion detection ---
    detections = detect_emotions(frame)

    if detections:
        top      = detections[0]
        box      = top["box"]        # [x, y, w, h]
        raw      = top["emotions"]   # FER's 7 raw scores

        # Map 7 FER emotions → 3 target emotions using group scores
        emotion, confidence = map_emotion(raw)

        # Always update display so the box tracks the face live
        display_box        = box
        display_emotion    = emotion
        display_confidence = confidence

        if confidence > MIN_CONFIDENCE:
            emotion_buffer.append(emotion)

            if emotion_buffer.count(emotion) >= STABLE_FRAMES:
                emotion_history.append(emotion)

                if emotion != last_emotion:
                    last_emotion       = emotion
                    emotion_start_time = time.time()

                # Trigger conversation only when stable, new, and not already talking
                if (emotion_start_time
                        and time.time() - emotion_start_time > 2
                        and not conversation_active):

                    print("Detected Emotion:", emotion)
                    emotion_start_time = time.time() + 1000  # block re-trigger
                    conversation_active = True

                    def _converse(e=emotion, h=list(emotion_history)):
                        global conversation_active
                        try:
                            run_conversation(e, h)
                        finally:
                            conversation_active = False

                    threading.Thread(target=_converse, daemon=True).start()

    # --- Draw bounding box + emotion label ---
    if display_box is not None and display_emotion is not None:
        x, y, w, h = display_box
        colour = EMOTION_COLOURS.get(display_emotion, (255, 255, 255))
        cv2.rectangle(frame, (x, y), (x + w, y + h), colour, 2)
        label = f"{display_emotion.upper()} ({display_confidence:.0%})"
        cv2.putText(frame, label, (x, max(y - 10, 15)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, colour, 2, cv2.LINE_AA)

    # --- Status bar ---
    status = "TALKING..." if conversation_active else "Press Q to quit"
    cv2.putText(frame, status, (10, frame.shape[0] - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1, cv2.LINE_AA)

    cv2.imshow("AI Companion", frame)

    if cv2.waitKey(1) == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()