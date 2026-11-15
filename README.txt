JARVIS 5.1 — WINDOWS VOICE + TEXT

1) Copy these files to your existing JARVIS folder (backup old jarvis.py).
2) Keep vosk-model-small-hi-0.22 folder there.
3) In VS Code PowerShell terminal, run:
   py -m pip install -r requirements.txt
   py -m py_compile jarvis.py
   py jarvis.py
4) Chat window opens. Type any message and press Enter.
5) Say Jarvis to wake microphone voice mode, then speak commands.
6) Uncheck Speak replies for text-only answers.
7) Ollama must be installed/running for local AI (ollama list; ollama serve if needed).
8) Online AI optional: config.json auto-created on first run; set online_ai=true and supply your own API key. Do not share API keys.

This is a tested-for-syntax project, not yet tested on your Windows PC.
Mic model or sounddevice errors should not prevent typing in the chat window.
To exit, close the window. Background startup scripts from older versions should be disabled while testing.
