"""
Prepare expanded RVC training dataset (v2).

Steps:
  1. Slice EN_sample.wav / EN_sample2.wav into ~5-second clips
  2. Generate targeted IndexTTS samples for phonemes RVC gets wrong
     (hurt/ɜːr, give/ɪv, make/eɪk and similar)
  3. Copy everything into voice_samples/rvc_training/

Usage:
    # IndexTTS must be running on port 9000 for step 2
    .venv\Scripts\python scripts\prepare_training_v2.py

    # Skip IndexTTS generation (slice EN_samples only):
    .venv\Scripts\python scripts\prepare_training_v2.py --no-tts
"""

from __future__ import annotations

import argparse
import base64
import sys
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
VOICE_SAMPLES = REPO_ROOT / "voice_samples"
OUT_DIR = VOICE_SAMPLES / "rvc_training"
INDEXTTS_URL = "http://127.0.0.1:9000"
TIMEOUT = 120.0

# Sentences targeting phonemes that RVC currently mispronounces.
# Focus: ɜːr (hurt/word/first), ɪv (give/live), eɪk (make/break/wake)
# Also adds general English coverage that the original set lacked.
TARGETED_SENTENCES: list[tuple[str, str]] = [
    # ɜːr phoneme — "hurt", "word", "first", "learn", "worth", "desert", "certain", "person"
    ("That actually hurt. I didn't expect that.", "sad"),
    ("Words can hurt more than you think.", "calm"),
    ("It hurts when you say things like that.", "sad"),
    ("First things first — are you okay?", "neutral"),
    ("I learned that the hard way.", "neutral"),
    ("Is it worth it? I'm not sure anymore.", "sad"),
    ("Turn around. I want to see your face.", "calm"),
    ("Burn it down or build it up. Your choice.", "neutral"),
    ("I heard what you said. Every word.", "neutral"),
    ("The worst part is, I still care.", "sad"),
    ("I would never desert you. Not ever.", "calm"),
    ("You deserve so much more than this.", "sad"),
    ("I'm certain. More certain than I've ever been.", "neutral"),
    ("Every person has a breaking point. I found mine.", "sad"),
    ("The hurt doesn't go away. It just changes shape.", "calm"),
    ("I didn't learn it from a book. I earned it.", "neutral"),
    ("Hurt me once and I remember. Hurt me twice and I'm done.", "neutral"),
    ("It hurts to watch you drift away like this.", "sad"),
    ("First I hurt, then I heal. That's just how it works.", "calm"),
    ("Her words cut deeper than she'll ever know.", "sad"),
    ("I deserved better and I knew it.", "neutral"),
    ("The first time it hurt, I forgave you. The third time, I stopped counting.", "sad"),
    ("Turn the hurt into something. Anything.", "neutral"),
    ("Worth the wait. Worth the hurt. Worth it all.", "happy"),
    ("I'm not the person I was. I've grown.", "calm"),
    # ɪv phoneme — "give", "live", "forgive"
    ("Give me a reason to stay.", "sad"),
    ("I'm not ready to forgive that yet.", "neutral"),
    ("You give too much and get nothing back.", "calm"),
    ("We live and we learn. That's all we can do.", "calm"),
    ("I'll give you one more chance. Just one.", "tsundere"),
    ("Don't give up on me. Please.", "sad"),
    ("Give it time. Things change.", "calm"),
    ("I forgive you. But I won't forget.", "neutral"),
    # eɪk phoneme — "make", "take", "wake", "break", "shake"
    ("Make it right before it's too late.", "neutral"),
    ("Take your time. I'll wait.", "calm"),
    ("Every mistake is something to learn from.", "calm"),
    ("I wake up and you're the first thing I think about.", "happy"),
    ("Don't break what we have. It matters.", "sad"),
    ("It takes courage to say that. I know.", "happy"),
    ("You can't fake that kind of feeling.", "neutral"),
    ("Make a decision. Any decision.", "neutral"),
    ("Take it or leave it.", "tsundere"),
    # Mixed — English sentences with broader consonant coverage
    ("That's the thing about trust — it breaks quietly.", "sad"),
    ("I don't give myself enough credit sometimes.", "neutral"),
    ("It takes more strength to stay than to leave.", "calm"),
    ("Make sure you take care of yourself too.", "happy"),
    ("I hurt people I care about without meaning to.", "sad"),
    ("First you hurt me, then you act like nothing happened.", "neutral"),
    ("Give me a break. I'm doing my best.", "tsundere"),
    ("Wake me up when things make sense again.", "sad"),
    ("Worth every second. Every single one.", "happy"),
    ("I'm learning to forgive myself too.", "calm"),
]



def check_ready(client: httpx.Client) -> bool:
    try:
        r = client.get(f"{INDEXTTS_URL}/ready", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def synthesize(client: httpx.Client, text: str, emotion: str, idx: int) -> bytes | None:
    try:
        r = client.post(
            f"{INDEXTTS_URL}/synthesize",
            json={
                "request_id": f"tgt_{idx:03d}",
                "text": text,
                "emotion": emotion,
                "emotion_intensity": 60,
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return base64.b64decode(r.json()["wav_base64"])
    except Exception as exc:
        print(f"  ERROR: {exc}")
        return None


def next_sample_idx(out_dir: Path) -> int:
    existing = sorted(out_dir.glob("sample_*.wav"))
    if not existing:
        return 1
    last = int(existing[-1].stem.split("_")[-1])
    return last + 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-tts", action="store_true", help="Skip IndexTTS generation")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # --- Step 1: Copy EN_sample.wav raw into rvc_training ---
    # Do NOT pre-slice — Applio's "Process data" step uses silence-aware chunking.
    # Fixed-interval slicing cuts mid-word and corrupts phoneme training.
    en_file = VOICE_SAMPLES / "EN_sample.wav"
    en_dest = OUT_DIR / "EN_sample.wav"
    if not en_file.exists():
        print(f"WARNING: {en_file.name} not found, skipping")
    elif en_dest.exists():
        print(f"EN_sample.wav already in dataset, skipping")
    else:
        import shutil
        shutil.copy2(str(en_file), str(en_dest))
        print(f"Copied EN_sample.wav -> {en_dest}")

    # --- Step 2: Targeted IndexTTS samples ---
    if args.no_tts:
        print("\n--no-tts flag set, skipping IndexTTS generation.")
        print(f"\nDataset ready in: {OUT_DIR}")
        _print_instructions()
        return

    with httpx.Client() as client:
        print(f"\nChecking IndexTTS at {INDEXTTS_URL}...")
        if not check_ready(client):
            print("ERROR: IndexTTS not ready. Start it first:")
            print("  .\\scripts\\easy_start_tts_adapter.ps1")
            print("Or re-run with --no-tts to skip this step.")
            sys.exit(1)
        print("IndexTTS ready.\n")

        start_idx = next_sample_idx(OUT_DIR)
        total = len(TARGETED_SENTENCES)
        ok = 0

        for i, (text, emotion) in enumerate(TARGETED_SENTENCES):
            idx = start_idx + i
            out_path = OUT_DIR / f"sample_{idx:03d}.wav"
            if out_path.exists():
                print(f"[{i+1:02d}/{total}] SKIP (exists): {text[:50]}")
                ok += 1
                continue

            print(f"[{i+1:02d}/{total}] {emotion:10s} | {text[:60]}")
            wav = synthesize(client, text, emotion, idx)
            if wav:
                out_path.write_bytes(wav)
                ok += 1
                print(f"           -> {len(wav):,} bytes -> {out_path.name}")
            else:
                print(f"           -> FAILED")
            time.sleep(0.2)

    print(f"\nTargeted samples: {ok}/{total}")
    print(f"Dataset ready in: {OUT_DIR}")
    _print_instructions()


def _print_instructions():
    applio_dataset = REPO_ROOT / "ApplioV3.6.2" / "assets" / "datasets" / "Koroki_v2"
    print(f"""
=== Next steps: retrain in Applio ===

1. Copy dataset into Applio:
   New-Item -ItemType Directory -Force "{applio_dataset}"
   Copy-Item "{OUT_DIR}\\*.wav" "{applio_dataset}\\"

2. Open Applio:
   cd ApplioV3.6.2
   .\\env\\python.exe app.py --open

3. In Applio — Train tab:
   - Model name:      Koroki_v2
   - Dataset path:    assets/datasets/Koroki_v2
   - Sample rate:     40000
   - Pretrained base: pretrained_v2/f0G40k.pth  (or load your existing Koroki model)
   - Epochs:          200  (or 150 if continuing from existing checkpoint)
   - Batch size:      8
   Click "Process data" first, then "Train model"

4. After training, copy new .pth + .index to adapters/singing/
   Update config/settings.yaml: rvc_model_path / rvc_index_path if names changed.
""")


if __name__ == "__main__":
    main()
