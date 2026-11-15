
import sounddevice as sd
import numpy as np

print("Microphone test starting...")
print("Ab 5 seconds tak kuch bolo!")

duration = 5
sample_rate = 16000

audio = sd.rec(
    int(duration * sample_rate),
    samplerate=sample_rate,
    channels=1,
    dtype="float32"
)

sd.wait()

volume = np.max(np.abs(audio))

print("Recording complete!")
print("Microphone volume:", round(float(volume), 4))

if volume > 0.01:
    print("SUCCESS! Microphone sound detect kar raha hai.")
else:
    print("Sound bahut low hai. Microphone settings check karo.")
