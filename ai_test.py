
import requests

url = "http://localhost:11434/api/generate"

data = {
    "model": "qwen3:1.7b",
    "prompt": "Reply in Roman Hinglish. Say: Namaste Sir, main JARVIS hoon.",
    "stream": False,
    "think": False,
    "options": {
        "num_predict": 60
    }
}

try:
    response = requests.post(url, json=data, timeout=180)
    response.raise_for_status()
    result = response.json()
    print("JARVIS AI:", result["response"])

except Exception as error:
    print("Error:", error)
