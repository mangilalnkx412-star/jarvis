import asyncio
import ctypes
import datetime
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import webbrowser

import edge_tts
import requests
import sounddevice as sd
from vosk import Model, KaldiRecognizer, SetLogLevel

# JARVIS 4.0 - Windows 11 / Python
BASE = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(BASE, "vosk-model-small-hi-0.22")
OLLAMA_MODEL = "qwen3:1.7b"
OLLAMA_URL = "http://localhost:11434/api/generate"
VOICE = "hi-IN-MadhurNeural"
RATE = 16000

WEBSITES = {
    "instagram": "https://www.instagram.com",
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "whatsapp": "https://web.whatsapp.com",
    "facebook": "https://www.facebook.com",
    "github": "https://github.com",
    "chatgpt": "https://chatgpt.com",
    "amazon": "https://www.amazon.in",
    "flipkart": "https://www.flipkart.com",
    "wikipedia": "https://www.wikipedia.org",
}

APP_COMMANDS = {
    "calculator": "calc.exe",
    "notepad": "notepad.exe",
    "paint": "mspaint.exe",
    "explorer": "explorer.exe",
    "task manager": "taskmgr.exe",
    "command prompt": "cmd.exe",
}

FOLDERS = {
    "desktop": "Desktop",
    "downloads": "Downloads",
    "documents": "Documents",
    "pictures": "Pictures",
    "music": "Music",
    "videos": "Videos",
}

WAKE_WORDS = ("जार्विस", "जारविस", "जारवेस", "जार्वीस", "जारविज", "jarvis")

AUDIO = queue.Queue(maxsize=40)
SPEAKING = threading.Event()
VOICE_LOCK = threading.Lock()
active = False

# Prevent two JARVIS processes listening simultaneously.
ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\JARVIS_4_INSTANCE")
if ctypes.windll.kernel32.GetLastError() == 183:
    print("JARVIS already running. Close the other window first.")
    sys.exit(0)

if not os.path.isdir(MODEL_DIR):
    print("Hindi Vosk model not found:", MODEL_DIR)
    sys.exit(1)

SetLogLevel(-1)
print("Loading Hindi voice recognition...")
recognizer = KaldiRecognizer(Model(MODEL_DIR), RATE)


def microphone_callback(indata, frames, time_info, status):
    if SPEAKING.is_set():
        return
    try:
        AUDIO.put_nowait(bytes(indata))
    except queue.Full:
        pass


def reset_microphone():
    while True:
        try:
            AUDIO.get_nowait()
        except queue.Empty:
            break
    recognizer.Reset()


async def make_voice(message, mp3_path):
    await edge_tts.Communicate(
        text=str(message), voice=VOICE, rate="-5%", pitch="-10Hz"
    ).save(mp3_path)


def play_mp3(mp3_path):
    # Write a separate .ps1 file, so Python triple-quote errors cannot occur.
    lines = [
        'param([string]$AudioPath)',
        '$ErrorActionPreference = "Stop"',
        'Add-Type -AssemblyName PresentationCore',
        '$p = New-Object System.Windows.Media.MediaPlayer',
        'try {',
        '  $p.Open([Uri]::new($AudioPath))',
        '  $limit = [DateTime]::UtcNow.AddSeconds(15)',
        '  while (-not $p.NaturalDuration.HasTimeSpan -and [DateTime]::UtcNow -lt $limit) {',
        '    Start-Sleep -Milliseconds 100',
        '  }',
        '  if (-not $p.NaturalDuration.HasTimeSpan) { throw "MP3 load failed" }',
        '  $seconds = $p.NaturalDuration.TimeSpan.TotalSeconds',
        '  $p.Volume = 1.0',
        '  $p.Play()',
        '  Start-Sleep -Milliseconds ([int](($seconds + 0.7) * 1000))',
        '} finally { $p.Close() }',
    ]
    ps1_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ps1", encoding="utf-8", delete=False) as f:
            ps1_path = f.name
            f.write("\n".join(lines))
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-STA", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", ps1_path, "-AudioPath", mp3_path],
            capture_output=True, text=True, timeout=60, creationflags=flags,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "PowerShell playback failed")
    finally:
        if ps1_path and os.path.exists(ps1_path):
            os.remove(ps1_path)


def speak(message):
    with VOICE_LOCK:
        print("JARVIS:", message)
        SPEAKING.set()
        mp3_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                mp3_path = f.name
            asyncio.run(make_voice(message, mp3_path))
            play_mp3(mp3_path)
        except Exception as error:
            print("VOICE ERROR:", error)
        finally:
            time.sleep(0.3)
            reset_microphone()
            SPEAKING.clear()
            if mp3_path and os.path.exists(mp3_path):
                os.remove(mp3_path)


def ollama(system, prompt, limit=140):
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": OLLAMA_MODEL, "system": system, "prompt": prompt,
            "stream": False, "think": False,
            "options": {"num_predict": limit, "temperature": 0.1},
        },
        timeout=120,
    )
    response.raise_for_status()
    return response.json().get("response", "").strip()


INTENT_SYSTEM = (
    "You classify spoken Hindi, Hinglish and English Windows assistant commands. "
    "Word order and grammar may be unusual. Return ONLY a JSON object with "
    "keys action and target. Allowed actions: open_website, open_app, "
    "open_folder, google_search, volume_up, volume_down, mute, screenshot, "
    "time, date, sleep, exit, chat, clarify. "
    "For open_website target is a familiar website name or explicitly spoken domain. "
    "For open_app target is the application name, e.g. chrome, excel, calculator. "
    "For open_folder target is desktop, downloads, documents, pictures, music or videos. "
    "For google_search target is the search query. For other actions target is empty. "
    "If unsure what action the user wants, use clarify. "
    "Do not invent actions or domains. Examples: "
    "'इंस्टा खोल दे' -> {\"action\":\"open_website\",\"target\":\"instagram\"}; "
    "'गूगल पर ACCA सर्च करो' -> {\"action\":\"google_search\",\"target\":\"ACCA\"}; "
    "'मेरे डाउनलोड दिखा' -> {\"action\":\"open_folder\",\"target\":\"downloads\"}; "
    "'एक्सेल चालू कर' -> {\"action\":\"open_app\",\"target\":\"excel\"}."
)


def understand(text):
    try:
        raw = ollama(INTENT_SYSTEM, text, 110)
        match = re.search(r"\{.*?\}", raw, re.DOTALL)
        if not match:
            return {"action": "clarify", "target": ""}
        obj = json.loads(match.group())
        action = str(obj.get("action", "clarify")).strip().lower()
        target = str(obj.get("target", "")).strip().lower()
        allowed = {
            "open_website", "open_app", "open_folder", "google_search",
            "volume_up", "volume_down", "mute", "screenshot", "time",
            "date", "sleep", "exit", "chat", "clarify",
        }
        if action not in allowed:
            action = "clarify"
        print("AI decision:", action, repr(target))
        return {"action": action, "target": target}
    except Exception as error:
        print("AI INTENT ERROR:", error)
        return {"action": "clarify", "target": ""}


def open_installed_app(name):
    name = name.lower().strip()
    if name in APP_COMMANDS:
        subprocess.Popen([APP_COMMANDS[name]])
        return True
    # Search installed Windows Start Menu shortcuts. Do not run arbitrary AI commands.
    roots = [
        os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs"),
        os.path.join(os.environ.get("PROGRAMDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs"),
    ]
    matches = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for directory, _, filenames in os.walk(root):
            for filename in filenames:
                if filename.lower().endswith(".lnk"):
                    stem = os.path.splitext(filename)[0].lower()
                    if stem == name:
                        matches.insert(0, os.path.join(directory, filename))
                    elif name in stem:
                        matches.append(os.path.join(directory, filename))
    if matches:
        os.startfile(matches[0])
        return True
    return False


def open_folder(name):
    if name not in FOLDERS:
        return False
    home = os.path.expanduser("~")
    folder = os.path.join(home, FOLDERS[name])
    # OneDrive may hold Desktop/Documents/Pictures instead.
    if not os.path.isdir(folder):
        onedrive = os.environ.get("OneDrive", "")
        folder = os.path.join(onedrive, FOLDERS[name])
    if not os.path.isdir(folder):
        return False
    os.startfile(folder)
    return True


def volume_key(key):
    # Windows multimedia virtual keys: volume up/down/mute.
    codes = {"volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD}
    code = codes[key]
    for _ in range(3 if key != "mute" else 1):
        ctypes.windll.user32.keybd_event(code, 0, 0, 0)
        ctypes.windll.user32.keybd_event(code, 0, 2, 0)


def screenshot():
    from PIL import ImageGrab
    folder = os.path.join(os.path.expanduser("~"), "Pictures")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "JARVIS_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".png")
    ImageGrab.grab().save(path)
    return path


def execute(intent, original):
    global active
    action = intent["action"]
    target = intent["target"]
    try:
        if action == "open_website":
            if target in WEBSITES:
                url = WEBSITES[target]
            elif re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", target):
                url = "https://" + target
            else:
                speak("सर, वेबसाइट का नाम फिर से बताइए।")
                return
            webbrowser.open(url)
            speak("जी सर, वेबसाइट खोल रहा हूँ।")
        elif action == "open_app":
            if open_installed_app(target):
                speak("जी सर, ऐप खोल रहा हूँ।")
            else:
                speak("सर, यह ऐप स्टार्ट मेन्यू में नहीं मिला।")
        elif action == "open_folder":
            if open_folder(target):
                speak("जी सर, फोल्डर खोल रहा हूँ।")
            else:
                speak("सर, यह फोल्डर नहीं मिला।")
        elif action == "google_search":
            if not target:
                speak("सर, क्या सर्च करना है?")
            else:
                webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote_plus(target))
                speak("जी सर, गूगल पर सर्च कर रहा हूँ।")
        elif action in ("volume_up", "volume_down", "mute"):
            volume_key(action)
            speak("जी सर, हो गया।")
        elif action == "screenshot":
            path = screenshot()
            print("Screenshot saved:", path)
            speak("जी सर, स्क्रीनशॉट सेव हो गया।")
        elif action == "time":
            speak("सर, अभी " + datetime.datetime.now().strftime("%I:%M %p") + " बजे हैं।")
        elif action == "date":
            speak("सर, आज " + datetime.datetime.now().strftime("%d %B %Y") + " है।")
        elif action == "sleep":
            speak("जी सर, स्लीप मोड चालू है।")
            active = False
            print("Status: SLEEP MODE")
        elif action == "exit":
            speak("जी सर, जार्विस बंद हो रहा है।")
            raise SystemExit(0)
        elif action == "chat":
            answer = ollama(
                "You are JARVIS. Reply briefly and helpfully in spoken Hindi, Devanagari script. "
                "Do not claim to perform actions that you cannot perform.", original, 150
            )
            speak(answer or "सर, जवाब नहीं मिला।")
        else:
            speak("सर, आपका मतलब समझ नहीं आया। एक बार फिर बोलिए।")
    except (OSError, requests.RequestException, RuntimeError, ImportError) as error:
        print("ACTION ERROR:", error)
        speak("सर, यह काम अभी पूरा नहीं हो पाया।")


def process_text(text):
    global active
    text = text.lower().strip()
    if not text:
        return
    print("You:", text)
    if not active:
        if any(word in text for word in WAKE_WORDS):
            active = True
            print("Status: ACTIVE")
            speak("जी सर, जार्विस ऑनलाइन है। बताइए।")
        return
    for word in WAKE_WORDS:
        text = text.replace(word, "")
    text = text.strip()
    if not text:
        return
    intent = understand(text)
    execute(intent, text)


def main():
    print("=" * 48)
    print("JARVIS 4.0 - SMART HINGLISH ASSISTANT")
    print("Recognition: Hindi Vosk")
    print("AI:", OLLAMA_MODEL)
    print("Voice:", VOICE)
    print("Status: SLEEP MODE")
    print("Say: Jarvis")
    print("Press Ctrl+C to stop")
    print("=" * 48)
    try:
        with sd.RawInputStream(
            samplerate=RATE, blocksize=8000, dtype="int16",
            channels=1, callback=microphone_callback
        ):
            while True:
                data = AUDIO.get()
                if recognizer.AcceptWaveform(data):
                    text = json.loads(recognizer.Result()).get("text", "").strip()
                    if text:
                        process_text(text)
    except KeyboardInterrupt:
        print("JARVIS stopped.")
    except Exception as error:
        print("JARVIS ERROR:", error)


if __name__ == "__main__":
    main()
