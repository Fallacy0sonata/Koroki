import requests
import base64

ADAPTER_URL = "http://127.0.0.1:9000/synthesize"

payload = {"text": "Hello, this is a short test from Koroki."}
resp = requests.post(ADAPTER_URL, json=payload, timeout=300)
resp.raise_for_status()
js = resp.json()
if "wav_base64" in js:
    data = base64.b64decode(js["wav_base64"])
    with open("out_adapter_test.wav", "wb") as f:
        f.write(data)
    print("WAV written to out_adapter_test.wav")
else:
    print("Unexpected response:", js)
