
import json
import queue
import sounddevice as sd
from vosk import Model, KaldiRecognizer

MODEL_PATH = "vosk-model-small-en-us-0.15"
SAMPLE_RATE = 16000

model = Model(MODEL_PATH)
recognizer = KaldiRecognizer(model, SAMPLE_RATE)

audio_queue = queue.Queue()

def audio_callback(indata, frames, time, status):
    if status:
        print(status)
    audio_queue.put(bytes(indata))

print("JARVIS Voice Recognition Ready!")
print("Say: Jarvis wake up")
print("Press Ctrl+C to stop.")

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
                result = json.loads(
                    recognizer.Result()
                )

                text = result.get("text", "")

                if text:
                    print("You said:", text)

except KeyboardInterrupt:
    print("\nVoice test stopped.")
