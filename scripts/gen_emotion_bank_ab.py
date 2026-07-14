"""A/B ear batch for the emotion reference bank (owner verdict material).

Per emotion, the same line is synthesized twice:
  A_instruct  — production path today (EN_sample + instruct2)
  B_bank      — bank reference + cross_lingual (the new path, flag currently off)

Needs the voice service up and loaded (POST :9004/load first — she may be
offloaded/asleep). Output: data/ear_batches/emotion_bank_ab/<emotion>_{A,B}.wav

Usage:  .venv\\Scripts\\python.exe scripts\\gen_emotion_bank_ab.py
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import requests

_ROOT = Path(__file__).resolve().parents[1]
_BANK = _ROOT / "voice_samples" / "emotion_bank"
_OUT = _ROOT / "data" / "ear_batches" / "emotion_bank_ab"
_URL = "http://127.0.0.1:9004/synthesize"

# One emotionally-loaded line per emotion — words alone shouldn't carry it;
# the DELIVERY difference is what the ears are judging.
_LINES = {
    "happy": "No way, it actually worked on the first try? Okay okay, today is a good day.",
    "sad": "Yeah... I mean, it's fine. It just would have been nice if you'd told me earlier.",
    "angry": "Are you serious right now? I literally just fixed that exact thing yesterday.",
    "fearful": "Wait. Did you hear that? Something in the logs is very, very wrong.",
    "surprised": "Hold on, WHAT? Since when do we have twenty thousand viewers?",
    "calm": "Mm, it's late. The rain sounds nice though. Let's just take it slow tonight.",
}
# her-bucket name the request sends (fearful lives under "afraid" in her map)
_REQ_EMOTION = {"fearful": "afraid"}


def synth(text: str, emotion: str, intensity: int, out: Path,
          audio_prompt: str | None = None, mode: str | None = None) -> bool:
    body = {"text": text, "emotion": emotion, "emotion_intensity": intensity,
            "request_id": out.stem}
    if audio_prompt:
        body["audio_prompt"] = audio_prompt
    if mode:
        body["synthesis_mode"] = mode
    r = requests.post(_URL, json=body, timeout=300)
    if r.status_code != 200:
        print(f"  [FAIL {r.status_code}] {out.name}: {r.text[:120]}")
        return False
    out.write_bytes(base64.b64decode(r.json()["wav_base64"]))
    print(f"  [ok] {out.name}")
    return True


def main() -> None:
    _OUT.mkdir(parents=True, exist_ok=True)
    results = {}
    for bank_emotion, line in _LINES.items():
        req_emotion = _REQ_EMOTION.get(bank_emotion, bank_emotion)
        refs = sorted(_BANK.glob(f"{bank_emotion}__actor*.wav"))
        if not refs:
            print(f"[skip] {bank_emotion}: no bank wav")
            continue
        a = synth(line, req_emotion, 75, _OUT / f"{bank_emotion}_A_instruct.wav")
        b = synth(line, req_emotion, 75, _OUT / f"{bank_emotion}_B_bank.wav",
                  audio_prompt=str(refs[0]), mode="cross_lingual")
        # bonus: every actor variant for this emotion, so the ear-check can
        # also pick WHICH actor's acting survives RVC best
        for ref in refs[1:]:
            actor = ref.stem.split("actor")[-1]
            synth(line, req_emotion, 75,
                  _OUT / f"{bank_emotion}_B_bank_actor{actor}.wav",
                  audio_prompt=str(ref), mode="cross_lingual")
        results[bank_emotion] = {"A": a, "B": b, "line": line}
    (_OUT / "batch_notes.json").write_text(json.dumps(results, indent=2))
    print(f"\nbatch: {_OUT}")
    print("verdict wanted: per emotion — does B carry the feeling better than A, "
          "and which actor variant sounds most like HER feeling it (not acting it)?")


if __name__ == "__main__":
    main()
