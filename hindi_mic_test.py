
import json
import os
import queue

import sounddevice as sd
from vosk import Model, KaldiRecognizer

MODEL_PATH = "vosk-model-small-hi-0.22"
SAMPLE_RATE = 16000

audio_queue = queue.Queue()

if not os.path.isdir(MODEL_PATH):
    print("ERROR: Hindi model folder nahi mila!")
    print("Expected folder:", os.path.abspath(MODEL_PATH))
    raise SystemExit

print("Hindi model load ho raha hai...")

model = Model(MODEL_PATH)
recognizer = KaldiRecognizer(model, SAMPLE_RATE)

def audio_callback(indata, frames, time_info, status):
    if status:
        print("Microphone status:", status)

    audio_queue.put(bytes(indata))

print("=" * 45)
print("JARVIS HINDI MICROPHONE TEST")
print("=" * 45)
print("Hindi mein bolo: Jarvis Utho")
print("Har sentence ke baad thoda pause karo.")
print("Stop karne ke liye Ctrl+C dabao.")
print("=" * 45)

try:
    with sd.RawInputStream(
        samplerate=SAMPLE_RATE,
        blocksize=8000,
        dtype="int16",
        channels=1,
        callback=audio_callback
    ):
        while True:
            data = audio_queue.get()

            if recognizer.AcceptWaveform(data):
                result = json.loads(recognizer.Result())
                text = result.get("text", "").strip()

                if text:
                    print("You (Hindi):", text, flush=True)

except KeyboardInterrupt:
    print("\nHindi microphone test stopped.")

except Exception as error:
    print("ERROR:", error)
