"""JARVIS 4.0 - Windows Hindi/Hinglish voice assistant.
Requirements: pip install vosk sounddevice edge-tts requests pillow
Run: py jarvis.py
"""
import asyncio
import ctypes
import datetime as dt
import json
import os
from pathlib import Path
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
from vosk import KaldiRecognizer, Model

ROOT = Path(__file__).resolve().parent
HINDI_MODEL = ROOT / 'vosk-model-small-hi-0.22'
ENGLISH_MODEL = ROOT / 'vosk-model-small-en-us-0.15'
MODEL_DIR = HINDI_MODEL if HINDI_MODEL.is_dir() else ENGLISH_MODEL
OLLAMA_MODEL = 'qwen3:1.7b'
OLLAMA_URL = 'http://127.0.0.1:11434/api/generate'
VOICE = 'hi-IN-MadhurNeural'
SAMPLE_RATE = 16000
AUDIO = queue.Queue(maxsize=32)
SPEAKING = threading.Event()
ACTIVE = False

WEBSITES = {
    'instagram': 'https://www.instagram.com',
    'youtube': 'https://www.youtube.com',
    'google': 'https://www.google.com',
    'gmail': 'https://mail.google.com',
    'whatsapp': 'https://web.whatsapp.com',
    'chatgpt': 'https://chatgpt.com',
    'amazon': 'https://www.amazon.in',
    'flipkart': 'https://www.flipkart.com',
    'wikipedia': 'https://www.wikipedia.org',
    'facebook': 'https://www.facebook.com',
    'reddit': 'https://www.reddit.com',
}
APPS = {
    'calculator': 'calc.exe',
    'notepad': 'notepad.exe',
    'paint': 'mspaint.exe',
    'explorer': 'explorer.exe',
    'settings': 'ms-settings:',
    'task manager': 'taskmgr.exe',
    'chrome': 'chrome.exe',
    'edge': 'msedge.exe',
    'word': 'winword.exe',
    'excel': 'excel.exe',
    'powerpoint': 'powerpnt.exe',
    'vscode': 'code',
}
FOLDERS = {
    'desktop': Path.home() / 'OneDrive' / 'Desktop',
    'downloads': Path.home() / 'Downloads',
    'documents': Path.home() / 'Documents',
    'pictures': Path.home() / 'Pictures',
    'music': Path.home() / 'Music',
    'videos': Path.home() / 'Videos',
}
WAKE = ('जार्विस', 'जारविस', 'जारवेस', 'जार्वीस', 'जारविज', 'jarvis')
ALLOWED_ACTIONS = {'website', 'app', 'folder', 'search', 'time', 'date',
                   'volume_up', 'volume_down', 'mute', 'screenshot',
                   'sleep', 'exit', 'chat', 'clarify'}

# Avoid accidentally running two copies (which can block the microphone).
if os.name == 'nt':
    _kernel = ctypes.windll.kernel32
    _kernel.CreateMutexW.restype = ctypes.c_void_p
    _mutex = _kernel.CreateMutexW(None, False, 'Local\\JARVIS_V4_SINGLE_INSTANCE')
    if _kernel.GetLastError() == 183:
        print('JARVIS already running. Close the other copy first.')
        sys.exit(1)

if not MODEL_DIR.is_dir():
    sys.exit('Vosk model missing. Extract vosk-model-small-hi-0.22 beside jarvis.py')
print('Loading speech model:', MODEL_DIR.name)
RECOGNIZER = KaldiRecognizer(Model(str(MODEL_DIR)), SAMPLE_RATE)


def microphone_callback(indata, frames, time_info, status):
    if SPEAKING.is_set():
        return
    try: