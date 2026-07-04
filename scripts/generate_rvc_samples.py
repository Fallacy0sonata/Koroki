"""
Generate RVC training samples from IndexTTS.

Saves to voice_samples/rvc_training/ and syncs to Applio dataset.
IndexTTS must be running on port 9000.

Usage:
    .venv\Scripts\python scripts\generate_rvc_samples.py
"""

from __future__ import annotations

import base64
import shutil
import sys
import time
from pathlib import Path

import httpx

INDEXTTS_URL = "http://127.0.0.1:9000"
OUT_DIR = Path(__file__).resolve().parents[1] / "voice_samples" / "rvc_training"
APPLIO_DATASET = Path(__file__).resolve().parents[1] / "ApplioV3.6.2" / "assets" / "datasets" / "Koroki_v2"
TIMEOUT = 120.0

SENTENCES: list[tuple[str, str]] = [
    # General varied speech — cadence, length, emotion coverage
    ("Good morning.", "neutral"),
    ("I was just thinking about you.", "happy"),
    ("Don't look at me like that.", "tsundere"),
    ("It's fine. I'm not upset.", "neutral"),
    ("I'll be right here, okay? I'm not going anywhere.", "calm"),
    ("That actually made me laugh. Stop it.", "happy"),
    ("You're being weird today. Even for you.", "tsundere"),
    ("I've been sitting here for a while now, just listening to the rain.", "calm"),
    ("What do you even want from me?", "neutral"),
    ("Okay, fine. Maybe I missed you a little. Just a little.", "tsundere"),
    ("The sky looks really pretty right now. You should see it.", "happy"),
    ("I don't want to talk about it.", "sad"),
    ("Hey. Are you listening to me?", "neutral"),
    ("I'm not going to apologize for that.", "tsundere"),
    ("Something feels off today. I can't explain it.", "sad"),
    ("You always do this. Every single time.", "neutral"),
    ("I'm fine. Really. Stop asking.", "sad"),
    ("We don't have to talk. We can just sit here.", "calm"),
    ("That was actually really sweet. Don't tell anyone I said that.", "happy"),
    ("I thought about what you said. You weren't entirely wrong.", "neutral"),
    ("It's not like I was waiting for you or anything.", "tsundere"),
    ("Sometimes I wonder what things would be like if things were different.", "sad"),
    ("Do you ever just feel like everything is too loud?", "calm"),
    ("I'm not going to pretend I understand. But I'm here.", "calm"),
    ("Why are you smiling like that? Stop it, it's creepy.", "tsundere"),
    ("I don't need your help. I said I was fine.", "neutral"),
    ("Look, I'm not great at this kind of thing. But I'm trying.", "neutral"),
    ("You're still here. Good.", "calm"),
    ("I keep thinking about that conversation we had. I can't stop.", "sad"),
    ("It's complicated. You wouldn't get it.", "neutral"),
    ("Don't make a big deal out of this. I just wanted to check on you.", "tsundere"),
    ("Everything's going to be okay. I really believe that.", "happy"),
    ("I don't like crowds. There are too many people.", "neutral"),
    ("Can you just be quiet for a second? I'm trying to think.", "neutral"),
    ("I never said I was perfect. I just said I was right.", "tsundere"),
    ("You're more important to me than you know.", "calm"),
    ("Stop looking at me. You're making me nervous.", "tsundere"),
    ("I had a dream last night. You were in it.", "calm"),
    ("I don't get attached. I just pay attention.", "neutral"),
    ("This is so stupid. Why do I even care?", "sad"),
    ("I've always been better at listening than talking.", "calm"),
    ("You deserve better than what you're settling for.", "calm"),
    ("Ugh, forget it. You never understand anyway.", "tsundere"),
    ("It's quiet. I like when it's this quiet.", "calm"),
    ("I was thinking we could try again. If you wanted.", "happy"),
    ("That's not what I meant and you know it.", "neutral"),
    ("Just because I'm here doesn't mean I like it.", "tsundere"),
    ("I don't know. I never know. That's kind of the problem.", "sad"),
    ("Hey. Look at me. It's going to be fine.", "happy"),
    ("I'll remember this. I remember everything.", "neutral"),
    ("I'm not scared. I'm just cautious. There's a difference.", "neutral"),
    ("Thank you. You didn't have to do that.", "happy"),
    ("I can't promise anything. But I can try.", "calm"),
    ("I don't want you to fix it. I just want you to listen.", "sad"),
    ("Stop making me feel things. It's inconvenient.", "tsundere"),
    ("I'm not going anywhere. Okay? I promise.", "calm"),

    # ɜːr phoneme — hurt / word / first / learn / worth / desert / certain
    ("That actually hurt. I didn't expect that.", "sad"),
    ("Words can hurt more than you think.", "calm"),
    ("It hurts when you say things like that.", "sad"),
    ("First things first — are you okay?", "neutral"),
    ("I learned that the hard way.", "neutral"),
    ("Is it worth it? I'm not sure anymore.", "sad"),
    ("Turn around. I want to see your face.", "calm"),
    ("I heard what you said. Every word.", "neutral"),
    ("The worst part is, I still care.", "sad"),
    ("I would never desert you. Not ever.", "calm"),
    ("You deserve so much more than this.", "sad"),
    ("I'm certain. More certain than I've ever been.", "neutral"),
    ("The hurt doesn't go away. It just changes shape.", "calm"),
    ("I didn't learn it from a book. I earned it.", "neutral"),
    ("Hurt me once and I remember. Hurt me twice and I'm done.", "neutral"),
    ("It hurts to watch you drift away like this.", "sad"),
    ("First I hurt, then I heal. That's just how it works.", "calm"),
    ("I deserved better and I knew it.", "neutral"),
    ("Worth the wait. Worth the hurt. Worth it all.", "happy"),
    ("I'm not the person I was. I've grown.", "calm"),
    ("Her words cut deeper than she'll ever know.", "sad"),
    ("Turn the hurt into something. Anything.", "neutral"),
    ("Every person has a breaking point. I found mine.", "sad"),

    # ɪv phoneme — give / live / forgive
    ("Give me a reason to stay.", "sad"),
    ("I'm not ready to forgive that yet.", "neutral"),
    ("You give too much and get nothing back.", "calm"),
    ("We live and we learn. That's all we can do.", "calm"),
    ("I'll give you one more chance. Just one.", "tsundere"),
    ("Don't give up on me. Please.", "sad"),
    ("Give it time. Things change.", "calm"),
    ("I forgive you. But I won't forget.", "neutral"),
    ("I give myself credit for trying.", "neutral"),
    ("Living with uncertainty is its own kind of strength.", "calm"),

    # eɪk phoneme — make / take / wake / break / shake
    ("Make it right before it's too late.", "neutral"),
    ("Take your time. I'll wait.", "calm"),
    ("Every mistake is something to learn from.", "calm"),
    ("I wake up and you're the first thing I think about.", "happy"),
    ("Don't break what we have. It matters.", "sad"),
    ("It takes courage to say that. I know.", "happy"),
    ("You can't fake that kind of feeling.", "neutral"),
    ("Make a decision. Any decision.", "neutral"),
    ("Take it or leave it.", "tsundere"),
    ("That's the thing about trust — it breaks quietly.", "sad"),
    ("Make sure you take care of yourself too.", "happy"),

    # Mixed — broader consonant and vowel coverage
    ("I don't give myself enough credit sometimes.", "neutral"),
    ("It takes more strength to stay than to leave.", "calm"),
    ("I hurt people I care about without meaning to.", "sad"),
    ("Give me a break. I'm doing my best.", "tsundere"),
    ("Wake me up when things make sense again.", "sad"),
    ("I'm learning to forgive myself too.", "calm"),
    ("First you hurt me, then you act like nothing happened.", "neutral"),
    ("Worth every second. Every single one.", "happy"),
    ("Take it or break it. Either way, something has to give.", "neutral"),
    ("I deserve someone who doesn't make me feel like I have to earn it.", "sad"),
    ("Hurt, heal, try again. That's the whole process.", "calm"),
    ("Make the most of what you have, first.", "neutral"),
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
                "request_id": f"rvc_gen_{idx:03d}",
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


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with httpx.Client() as client:
        print(f"Checking IndexTTS at {INDEXTTS_URL}...")
        if not check_ready(client):
            print("ERROR: IndexTTS not ready. Start it first:")
            print("  .\\scripts\\easy_start_tts_adapter.ps1")
            sys.exit(1)
        print("IndexTTS ready.\n")

        total = len(SENTENCES)
        ok = 0
        for i, (text, emotion) in enumerate(SENTENCES, 1):
            out_path = OUT_DIR / f"sample_{i:03d}.wav"
            if out_path.exists():
                print(f"[{i:03d}/{total}] SKIP (exists): {text[:50]}")
                ok += 1
                continue

            print(f"[{i:03d}/{total}] {emotion:10s} | {text[:60]}")
            wav = synthesize(client, text, emotion, i)
            if wav:
                out_path.write_bytes(wav)
                ok += 1
                print(f"           -> {len(wav):,} bytes")
            else:
                print(f"           -> FAILED")
            time.sleep(0.2)

    print(f"\nDone: {ok}/{total} samples saved to {OUT_DIR}")

    # Sync to Applio dataset
    if APPLIO_DATASET.exists():
        synced = 0
        for f in OUT_DIR.glob("sample_*.wav"):
            dest = APPLIO_DATASET / f.name
            if not dest.exists():
                shutil.copy2(str(f), str(dest))
                synced += 1
        print(f"Synced {synced} new files to Applio dataset")
        print(f"Total in dataset: {len(list(APPLIO_DATASET.glob('*.wav')))}")


if __name__ == "__main__":
    main()
