"""JARVIS 5.0 — Windows voice assistant; explicit allowlisted actions only."""
import ast
import asyncio
import ctypes
import datetime as dt
import json
import math
import operator
import os
from pathlib import Path
import queue
import re
import subprocess
import tempfile
import threading
import time
import urllib.parse
import webbrowser
import tkinter as tk
from tkinter import ttk, scrolledtext

import requests
import sounddevice as sd
from vosk import Model, KaldiRecognizer, SetLogLevel

ROOT = Path(__file__).resolve().parent
DEFAULT = {"online_ai": False, "openai_api_key": "", "openai_model": "gpt-4.1-mini",
           "ollama_model": "qwen3:1.7b", "hindi_voice": "hi-IN-MadhurNeural",
           "vosk_model": "vosk-model-small-hi-0.22"}
config_path = ROOT / "config.json"
if not config_path.exists():
    config_path.write_text(json.dumps(DEFAULT, indent=2), encoding="utf-8")
try:
    CONFIG = {**DEFAULT, **json.loads(config_path.read_text(encoding="utf-8"))}
except (OSError, ValueError) as exc:
    raise SystemExit(f"config.json invalid: {exc}")

AUDIO = queue.Queue(maxsize=60)
SPEAKING = threading.Event()
ACTIVE = False
RECOGNIZER = None
HISTORY = []
UI_EVENTS = queue.Queue()
VOICE_ENABLED = threading.Event()
VOICE_ENABLED.set()
STOP = threading.Event()
WORK_LOCK = threading.RLock()
WAKE = ("जार्विस", "जारविस", "जारवेस", "जार्वीस", "jarvis")
SITES = {"instagram": "https://www.instagram.com", "youtube": "https://www.youtube.com",
         "google": "https://www.google.com", "gmail": "https://mail.google.com",
         "whatsapp": "https://web.whatsapp.com", "chatgpt": "https://chatgpt.com",
         "wikipedia": "https://www.wikipedia.org", "amazon": "https://www.amazon.in",
         "flipkart": "https://www.flipkart.com"}
APPS = {"calculator": "calc.exe", "notepad": "notepad.exe", "paint": "mspaint.exe",
        "explorer": "explorer.exe", "settings": "ms-settings:"}
FOLDERS = {"downloads": Path.home() / "Downloads", "documents": Path.home() / "Documents",
           "pictures": Path.home() / "Pictures", "desktop": Path.home() / "Desktop"}
ALIASES = {"इंस्टाग्राम": "instagram", "इंस्टा": "instagram", "यूट्यूब": "youtube",
           "गूगल": "google", "जीमेल": "gmail", "व्हाट्सएप": "whatsapp",
           "कैलकुलेटर": "calculator", "नोटपैड": "notepad", "पेंट": "paint",
           "डाउनलोड": "downloads", "डॉक्यूमेंट": "documents", "डेस्कटॉप": "desktop",
           "फोटो": "pictures", "क्रोम": "chrome", "एक्सेल": "excel", "वर्ड": "word"}

# Windows voice: use PowerShell WPF for Edge TTS MP3, SAPI fallback.
PLAYER = r'''
param([string]$AudioPath)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationCore
$p = New-Object System.Windows.Media.MediaPlayer
try {
    $p.Open([Uri]::new($AudioPath))
    $limit = [DateTime]::UtcNow.AddSeconds(12)
    while (-not $p.NaturalDuration.HasTimeSpan -and [DateTime]::UtcNow -lt $limit) {
        Start-Sleep -Milliseconds 100
    }
    if (-not $p.NaturalDuration.HasTimeSpan) { throw 'Audio load failed' }
    $seconds = $p.NaturalDuration.TimeSpan.TotalSeconds
    $p.Volume = 1
    $p.Play()
    Start-Sleep -Milliseconds ([int](($seconds + 0.6) * 1000))
} finally { $p.Close() }
'''

def speak(message):
    message = str(message).strip()[:650]
    if not message:
        return
    print("JARVIS:", message, flush=True)
    UI_EVENTS.put(("assistant", message))
    if not VOICE_ENABLED.is_set():
        return
    SPEAKING.set()
    temp = None
    try:
        import edge_tts
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as handle:
            temp = Path(handle.name)
        async def make_voice():
            await edge_tts.Communicate(message, CONFIG["hindi_voice"], rate="-5%").save(str(temp))
        asyncio.run(make_voice())
        # Pass the path as a separate PowerShell argument, not injected into script.
        script = "& { " + PLAYER + " } -AudioPath '" + str(temp).replace("'", "''") + "'"
        result = subprocess.run(["powershell.exe", "-NoProfile", "-STA", "-NonInteractive",
                                 "-Command", script], capture_output=True, text=True,
                                timeout=50, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
    except Exception as exc:
        print("Online voice unavailable; Windows voice fallback:", exc)
        try:
            import win32com.client
            voice = win32com.client.Dispatch("SAPI.SpVoice")
            voice.Speak(message)
        except Exception as fallback_exc:
            print("Voice fallback error:", fallback_exc)
    finally:
        if temp:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
        time.sleep(0.3)
        reset_audio()
        SPEAKING.clear()

def reset_audio():
    while True:
        try:
            AUDIO.get_nowait()
        except queue.Empty:
            break
    if RECOGNIZER is not None:
        RECOGNIZER.Reset()

def microphone_callback(indata, frames, time_info, status):
    if status:
        print("Mic:", status)
    if not SPEAKING.is_set():
        try:
            AUDIO.put_nowait(bytes(indata))
        except queue.Full:
            pass

def normalize(text):
    text = text.lower().strip()
    for source, target in ALIASES.items():
        text = text.replace(source, target)
    return text

def local_ai(prompt, system, limit=160):
    payload = {"model": CONFIG["ollama_model"], "system": system, "prompt": prompt,
               "stream": False, "think": False, "options": {"num_predict": limit, "temperature": 0.15}}
    response = requests.post("http://127.0.0.1:11434/api/generate", json=payload, timeout=100)
    response.raise_for_status()
    return response.json().get("response", "").strip()

def online_ai(prompt, system):
    key = CONFIG.get("openai_api_key", "") or os.environ.get("OPENAI_API_KEY", "")
    if not CONFIG.get("online_ai") or not key:
        raise RuntimeError("Online AI not configured")
    response = requests.post("https://api.openai.com/v1/chat/completions",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        json={"model": CONFIG["openai_model"], "messages": [
            {"role": "system", "content": system}, {"role": "user", "content": prompt}],
              "max_tokens": 260}, timeout=35)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()

def ai(prompt, system, limit=160):
    if CONFIG.get("online_ai"):
        try:
            return online_ai(prompt, system)
        except Exception as exc:
            print("Online AI fallback:", exc)
    return local_ai(prompt, system, limit)

INTENT_SYSTEM = '''Classify Hindi, Hinglish and English voice commands for a Windows assistant.
Return ONLY JSON with keys action, target, query. Allowed action values:
open_website, open_app, open_folder, search_web, calculate, get_time, get_date,
volume_up, volume_down, screenshot, sleep, exit, chat, clarify.
For open_website target MUST be one of instagram,youtube,google,gmail,whatsapp,chatgpt,wikipedia,amazon,flipkart.
For open_app target MUST be one of calculator,notepad,paint,explorer,settings,chrome,excel,word,powerpoint.
For open_folder target MUST be one of downloads,documents,pictures,desktop.
For search_web put search terms in query. For calculate put a numeric expression in query.
If unsure use clarify. Never invent actions or execute system commands.
Examples:
"insta dikha" -> {"action":"open_website","target":"instagram","query":""}
"google pe acca search karo" -> {"action":"search_web","target":"","query":"acca"}
"pachis plus pandrah" -> {"action":"calculate","target":"","query":"25+15"}
"mera downloads dikhao" -> {"action":"open_folder","target":"downloads","query":""}
"so jao" -> {"action":"sleep","target":"","query":""}
"kya hota hai GST" -> {"action":"chat","target":"","query":""}'''
ACTIONS = {"open_website", "open_app", "open_folder", "search_web", "calculate",
           "get_time", "get_date", "volume_up", "volume_down", "screenshot",
           "sleep", "exit", "chat", "clarify"}

def understand(text):
    normalized = normalize(text)
    # Fast direct matching for common typed commands; AI handles flexible wording.
    if re.fullmatch(r"[0-9\s.+*/()%\-^×÷]+", normalized):
        return "calculate", "", normalized
    if any(x in normalized for x in ("gst", "cagr", "compound interest", "percentage")) and re.search(r"\d", normalized):
        return "calculate", "", normalized
    try:
        raw = ai(normalized, INTENT_SYSTEM, 125)
        match = re.search(r"\{.*?\}", raw, re.S)
        data = json.loads(match.group(0)) if match else {}
        action = data.get("action", "clarify")
        target = str(data.get("target", "")).lower().strip()
        query_text = str(data.get("query", "")).strip()
        if action not in ACTIONS:
            action = "clarify"
        if action == "open_website" and target not in SITES:
            action = "clarify"
        if action == "open_app" and target not in {*APPS, "chrome", "excel", "word", "powerpoint"}:
            action = "clarify"
        if action == "open_folder" and target not in FOLDERS:
            action = "clarify"
        return action, target, query_text
    except Exception as exc:
        print("Intent AI error:", exc)
        # Simple fallback when Ollama is unavailable.
        for name in SITES:
            if name in normalized and any(x in normalized for x in ("open", "khol", "खोल", "dikha", "दिखा")):
                return "open_website", name, ""
        return "clarify", "", ""

BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
          ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
          ast.Mod: operator.mod, ast.Pow: operator.pow}
UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}

def safe_math(expression):
    """Evaluate only numeric AST, never Python code or functions."""
    if len(expression) > 120:
        raise ValueError("Expression too long")
    expression = expression.replace("×", "*").replace("÷", "/").replace("^", "**")
    tree = ast.parse(expression, mode="eval")
    def evaluate(node, depth=0):
        if depth > 15:
            raise ValueError("Too complex")
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            if abs(node.value) > 1e12:
                raise ValueError("Number too large")
            return node.value
        if isinstance(node, ast.UnaryOp) and type(node.op) in UNARY:
            return UNARY[type(node.op)](evaluate(node.operand, depth+1))
        if isinstance(node, ast.BinOp) and type(node.op) in BINOPS:
            left, right = evaluate(node.left, depth+1), evaluate(node.right, depth+1)
            if isinstance(node.op, ast.Pow) and (abs(right) > 12 or abs(left) > 1e8):
                raise ValueError("Power too large")
            result = BINOPS[type(node.op)](left, right)
            if not math.isfinite(result) or abs(result) > 1e15:
                raise ValueError("Result too large")
            return result
        raise ValueError("Only numbers and arithmetic operators allowed")
    return evaluate(tree.body)

def finance_answer(text):
    """Exact numeric finance requests; use simple clear patterns."""
    t = normalize(text).replace(",", "")
    numbers = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", t)]
    if len(numbers) < 2:
        return None
    if any(x in t for x in ("gst", "जीएसटी")):
        amount, rate = numbers[:2]
        gst = amount * rate / 100
        return f"सर, {rate:g} प्रतिशत जी एस टी {gst:,.2f} रुपये है। कुल {amount + gst:,.2f} रुपये।"
    if any(x in t for x in ("percent", "percentage", "प्रतिशत", "%")):
        amount, rate = numbers[:2]
        return f"सर, {amount:g} का {rate:g} प्रतिशत {amount * rate / 100:,.2f} है।"
    if "cagr" in t and len(numbers) >= 3:
        start, end, years = numbers[:3]
        if start > 0 and end > 0 and years > 0:
            return f"सर, सी ए जी आर {((end / start) ** (1 / years) - 1) * 100:.2f} प्रतिशत है।"
    if any(x in t for x in ("compound interest", "चक्रवृद्धि")) and len(numbers) >= 3:
        principal, rate, years = numbers[:3]
        total = principal * (1 + rate / 100) ** years
        return f"सर, कुल रकम {total:,.2f} और ब्याज {total - principal:,.2f} रुपये है।"
    return None

def open_installed_app(target):
    if target in APPS:
        if target == "settings":
            os.startfile("ms-settings:")
        else:
            subprocess.Popen([APPS[target]])
        return
    # Only known application executables; Windows resolves if installed / on PATH.
    candidates = {"chrome": ["chrome.exe"], "excel": ["excel.exe"],
                  "word": ["winword.exe"], "powerpoint": ["powerpnt.exe"]}
    if target in candidates:
        for executable in candidates[target]:
            try:
                subprocess.Popen([executable])
                return
            except OSError:
                continue
    raise FileNotFoundError(f"{target} not found")

def adjust_volume(up):
    # Standard Windows media key via SendInput alternative: keybd_event.
    key = 0xAF if up else 0xAE
    for _ in range(3):
        ctypes.windll.user32.keybd_event(key, 0, 0, 0)
        ctypes.windll.user32.keybd_event(key, 0, 2, 0)

def run_action(action, target, query_text, original):
    global ACTIVE
    print("AI decision:", action, target, query_text, flush=True)
    try:
        if action == "open_website":
            webbrowser.open(SITES[target]); speak(f"जी सर, {target} खोल रहा हूँ।")
        elif action == "open_app":
            open_installed_app(target); speak(f"जी सर, {target} खोल रहा हूँ।")
        elif action == "open_folder":
            folder = FOLDERS[target]
            if not folder.exists():
                speak("सर, यह फोल्डर नहीं मिला।")
            else:
                os.startfile(str(folder)); speak(f"जी सर, {target} खोल रहा हूँ।")
        elif action == "search_web":
            if not query_text:
                speak("सर, क्या सर्च करना है?")
            else:
                webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote(query_text))
                speak("जी सर, गूगल पर सर्च कर रहा हूँ।")
        elif action == "calculate":
            financial = finance_answer(original)
            if financial:
                speak(financial)
            else:
                try:
                    result = safe_math(query_text)
                    speak(f"सर, जवाब है {result:,.6g}")
                except (ValueError, SyntaxError, ZeroDivisionError, OverflowError):
                    speak("सर, गणित का सवाल साफ़ नहीं समझ आया। कृपया दोबारा बोलिए।")
        elif action == "get_time":
            speak("सर, अभी समय है " + dt.datetime.now().strftime("%I:%M %p"))
        elif action == "get_date":
            speak("सर, आज तारीख है " + dt.datetime.now().strftime("%d %B %Y"))
        elif action in ("volume_up", "volume_down"):
            adjust_volume(action == "volume_up"); speak("जी सर, आवाज़ बदल दी।")
        elif action == "screenshot":
            from PIL import ImageGrab
            folder = ROOT / "screenshots"; folder.mkdir(exist_ok=True)
            file = folder / ("screen_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S") + ".png")
            ImageGrab.grab().save(file)
            speak("सर, स्क्रीनशॉट सेव कर दिया।")
            print("Screenshot:", file)
        elif action == "sleep":
            speak("जी सर, स्लीप मोड चालू।"); ACTIVE = False; UI_EVENTS.put(("status", "Sleeping — say Jarvis"))
        elif action == "exit":
            speak("जी सर, जार्विस बंद हो रहा है।"); STOP.set(); UI_EVENTS.put(("quit", "")); return
        elif action == "chat":
            try:
                history = "\n".join(HISTORY[-4:])
                answer = ai(original + "\nRecent conversation:\n" + history,
                    "You are JARVIS, a helpful Windows assistant. Reply briefly in simple Hindi Devanagari. "
                    "Do not claim to have performed actions you cannot perform.", 190)
                HISTORY.append("User: " + original)
                HISTORY.append("Assistant: " + answer)
                speak(answer)
            except Exception as exc:
                print("Chat error:", exc)
                speak("सर, ए आई ब्रेन से कनेक्शन नहीं हो पाया। ओलामा चालू है क्या?")
        else:
            speak("सर, बात पूरी तरह समझ नहीं आई। ज़रा दूसरे शब्दों में बताइए।")
    except SystemExit:
        raise
    except Exception as exc:
        print("Action error:", exc)
        speak("सर, यह काम पूरा नहीं हो पाया। टर्मिनल में एरर दिख रहा है।")

def handle_text(text):
    global ACTIVE
    print("You:", text, flush=True)
    UI_EVENTS.put(("user", text))
    lower = text.lower().strip()
    if not ACTIVE:
        if any(w in lower for w in WAKE):
            ACTIVE = True
            UI_EVENTS.put(("status", "Voice active"))
            speak("जी सर, जार्विस ऑनलाइन है। बताइए।")
        return
    for word in WAKE:
        lower = lower.replace(word, "")
    lower = lower.strip()
    if not lower:
        return
    action, target, query_text = understand(lower)
    run_action(action, target, query_text, lower)



def text_command(message):
    """Typed messages execute directly; wake word only applies to microphone."""
    message = message.strip()
    if not message:
        return
    UI_EVENTS.put(("user", message))
    with WORK_LOCK:
        try:
            action, target, query_text = understand(message)
            run_action(action, target, query_text, message)
        except Exception as exc:
            UI_EVENTS.put(("assistant", "Error: " + str(exc)))


def microphone_worker():
    global RECOGNIZER
    model_path = ROOT / CONFIG["vosk_model"]
    if not model_path.is_dir():
        UI_EVENTS.put(("status", "Mic unavailable: Hindi Vosk model missing"))
        return
    try:
        SetLogLevel(-1)
        RECOGNIZER = KaldiRecognizer(Model(str(model_path)), 16000)
        UI_EVENTS.put(("status", "Mic ready — say Jarvis"))
        with sd.RawInputStream(samplerate=16000, blocksize=8000, dtype="int16",
                               channels=1, callback=microphone_callback):
            while not STOP.is_set():
                try:
                    data = AUDIO.get(timeout=0.3)
                except queue.Empty:
                    continue
                if RECOGNIZER.AcceptWaveform(data):
                    phrase = json.loads(RECOGNIZER.Result()).get("text", "").strip()
                    if phrase:
                        with WORK_LOCK:
                            handle_text(phrase)
    except Exception as exc:
        UI_EVENTS.put(("status", "Microphone error: " + str(exc)))


def start_gui():
    root = tk.Tk()
    root.title("JARVIS 5.1 — Hybrid AI | Voice + Text")
    root.geometry("820x610")
    root.minsize(550, 420)
    root.configure(bg="#101826")

    heading = tk.Frame(root, bg="#172338", padx=18, pady=12)
    heading.pack(fill="x")
    tk.Label(heading, text="JARVIS 5.1", fg="#63d9ff", bg="#172338",
             font=("Segoe UI", 19, "bold")).pack(side="left")
    status_var = tk.StringVar(value="Starting microphone...")
    tk.Label(heading, textvariable=status_var, fg="#a9d8e8", bg="#172338",
             font=("Segoe UI", 10)).pack(side="right")

    chat = scrolledtext.ScrolledText(root, wrap="word", bg="#0e1522", fg="#f2f5fb",
                                     insertbackground="white", relief="flat",
                                     font=("Segoe UI", 11), padx=15, pady=15)
    chat.pack(fill="both", expand=True, padx=15, pady=(15, 8))
    chat.tag_configure("user", foreground="#7acbff", spacing1=12, spacing3=7)
    chat.tag_configure("assistant", foreground="#d2ffde", spacing1=12, spacing3=7)
    chat.tag_configure("system", foreground="#a9b4c5", spacing1=12, spacing3=7)
    chat.insert("end", "JARVIS: Type a message below, or say 'Jarvis' to activate voice.\n", "system")
    chat.configure(state="disabled")

    controls = tk.Frame(root, bg="#101826")
    controls.pack(fill="x", padx=15, pady=(0, 15))
    entry = tk.Entry(controls, font=("Segoe UI", 12), bg="#223149", fg="white",
                     insertbackground="white", relief="flat")
    entry.pack(side="left", fill="x", expand=True, ipady=10)

    def submit(event=None):
        msg = entry.get().strip()
        if not msg:
            return
        entry.delete(0, "end")
        threading.Thread(target=text_command, args=(msg,), daemon=True).start()

    tk.Button(controls, text="Send", command=submit, bg="#1488b8", fg="white",
              relief="flat", font=("Segoe UI", 11, "bold"), padx=15).pack(side="left", padx=8, ipady=7)
    entry.bind("<Return>", submit)

    voice_var = tk.BooleanVar(value=True)
    def toggle_voice():
        if voice_var.get():
            VOICE_ENABLED.set()
        else:
            VOICE_ENABLED.clear()
    tk.Checkbutton(controls, text="Speak replies", variable=voice_var, command=toggle_voice,
                   bg="#101826", fg="white", selectcolor="#223149",
                   activebackground="#101826", activeforeground="white").pack(side="left")

    def pump():
        try:
            while True:
                kind, message = UI_EVENTS.get_nowait()
                if kind == "quit":
                    close()
                    return
                if kind == "status":
                    status_var.set(message)
                    continue
                chat.configure(state="normal")
                chat.insert("end", ("YOU: " if kind == "user" else "JARVIS: ") + message + "\n",
                            kind if kind in ("user", "assistant") else "system")
                chat.see("end")
                chat.configure(state="disabled")
        except queue.Empty:
            pass
        if not STOP.is_set():
            root.after(120, pump)

    def close():
        STOP.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    root.after(120, pump)
    threading.Thread(target=microphone_worker, daemon=True).start()
    entry.focus_set()
    root.mainloop()


if __name__ == "__main__":
    start_gui()
