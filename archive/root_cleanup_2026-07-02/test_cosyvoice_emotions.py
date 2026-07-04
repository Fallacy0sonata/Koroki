"""
Test CosyVoice emotion keywords — generates one WAV per emotion using the same sentence.
Run from Koroki root: .venv_cosyvoice\Scripts\python.exe test_cosyvoice_emotions.py
"""

import base64
import json
import urllib.request

BASE = "http://127.0.0.1:9004"
TEXT = "I can't believe you actually did that."

EMOTIONS = ["neutral", "happy", "sad", "angry", "excited", "calm", "shy", "surprised", "gentle"]

def synthesize(text: str, emotion: str) -> bytes:
    payload = json.dumps({"text": text, "emotion": emotion}).encode()
    req = urllib.request.Request(
        f"{BASE}/synthesize",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    return base64.b64decode(data["wav_base64"])

def main():
    try:
        with urllib.request.urlopen(f"{BASE}/ready", timeout=5) as r:
            print("Adapter ready:", json.loads(r.read()))
    except Exception as e:
        print(f"Adapter not ready: {e}")
        return

    print(f"\nText: {TEXT!r}\n")
    for emotion in EMOTIONS:
        out = f"test_emotion_{emotion}.wav"
        try:
            wav = synthesize(TEXT, emotion)
            with open(out, "wb") as f:
                f.write(wav)
            duration = len(wav) / (24000 * 2)
            print(f"  [{emotion:10s}] {duration:.2f}s → {out}")
        except Exception as e:
            print(f"  [{emotion:10s}] FAILED: {e}")

if __name__ == "__main__":
    main()
