"""
Generate Koroki voice training data using IndexTTS (English/Chinese output).

IndexTTS produces perfect clean Koroki-voice audio in EN/ZH. We collect 1-2 hours
of this as training data for Style-Bert-VITS2, which can then synthesize Japanese
with Koroki's learned voice characteristics.

Output layout:
  data/sbvits2_training/
    wavs/        ← WAV files (22050 Hz mono)
    metadata.csv ← transcript (file|text format for SBVITS2)

Usage:
  .venv_indextts\Scripts\python.exe experiments\style-bert-vits2\gen_indexTTS_training_data.py
  [--tts-url http://127.0.0.1:9000] [--target-hours 1.5] [--output data/sbvits2_training]
"""

from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import time
import urllib.request
import urllib.error
from pathlib import Path

import numpy as np
import soundfile as sf

# ---------------------------------------------------------------------------
# Phonetically diverse English sentences covering a wide range of phonemes,
# prosody patterns, and sentence types. Designed to give SBVITS2 maximum
# voice characteristic coverage from minimal utterances.
# ---------------------------------------------------------------------------
SENTENCES = [
    # Everyday conversational
    "Hey, what do you think about that?",
    "I was just thinking the same thing.",
    "That sounds really interesting to me.",
    "Oh, I didn't realize that at all.",
    "Wait, can you say that again please?",
    "Honestly, I have no idea what happened.",
    "That's actually pretty funny when you think about it.",
    "I really wasn't expecting that to happen.",
    "You know what, you might be right.",
    "Let me think about that for a second.",
    "I'm not so sure about that one.",
    "That makes a lot of sense actually.",
    "Yeah, I totally get what you mean.",
    "Oh wow, I hadn't thought of it that way.",
    "That's a good point, I'll give you that.",
    "Hmm, I'm going to have to think on this.",
    "You know, it's funny how things work out.",
    "Sometimes life just throws you a curveball.",
    "I feel like we've talked about this before.",
    "Anyway, let's not get too far off topic.",

    # Questions and curiosity
    "Why do you think that happens so often?",
    "What would you do if you were in that situation?",
    "Have you ever tried something like that before?",
    "Do you really believe that's true?",
    "Where do you think it all went wrong?",
    "How long has this been going on for?",
    "Who would have thought it would end up like this?",
    "When did you first notice something was off?",
    "Is there anything else I should know about?",
    "Could you explain that a little more clearly?",
    "Would you say that's a common thing?",
    "Are you absolutely sure about that?",
    "What exactly are you trying to say here?",
    "How do you feel about everything that happened?",
    "Why does it always seem to work that way?",

    # Descriptions and observations
    "The sky was an unusual shade of orange that evening.",
    "Everything about that moment felt completely surreal.",
    "There's something calming about the sound of rain.",
    "The city lights glitter like scattered diamonds at night.",
    "She walked into the room and everything changed.",
    "It was the kind of silence that felt heavy and thick.",
    "The wind carried the scent of pine trees and earth.",
    "Every detail of that day is burned into my memory.",
    "Time seems to move differently when you're waiting.",
    "The old house stood at the end of a long winding road.",
    "Something about the way she laughed made everyone smile.",
    "The music drifted through the open window like smoke.",
    "It was a small thing, but it meant everything to me.",
    "The stars looked impossibly bright out in the countryside.",
    "Every step forward felt like it cost something.",

    # Emotional range
    "I'm so glad you're here with me right now.",
    "That really caught me off guard, I won't lie.",
    "I feel so relieved that everything worked out fine.",
    "Honestly, I was terrified the whole time.",
    "I can't believe how lucky we are.",
    "That was honestly one of the best days of my life.",
    "I'm still processing everything that happened.",
    "I was so angry I could barely speak.",
    "There was something bittersweet about the whole thing.",
    "It felt like the world had finally shifted back into place.",
    "I was so embarrassed I wanted to disappear.",
    "Something about that made me feel deeply sad.",
    "I've never felt more alive than I did in that moment.",
    "The relief was almost overwhelming.",
    "I'm proud of how far we've come together.",

    # Longer and more complex
    "There are moments in life when everything suddenly becomes clear and you understand things you never did before.",
    "It's strange how a single conversation can completely change the way you see the world.",
    "Sometimes the most important things are the ones you almost overlooked.",
    "The best relationships are built on honesty, trust, and a willingness to be vulnerable.",
    "Looking back, I realize that all the difficult moments were actually preparing me for something better.",
    "There's a particular kind of loneliness that comes from being surrounded by people who don't understand you.",
    "The truth is, most of the things we worry about never actually happen.",
    "Courage isn't the absence of fear, it's deciding that something else is more important.",
    "Every experience, good or bad, teaches you something if you're paying attention.",
    "The difference between where you are and where you want to be is what you're willing to do.",

    # Short and punchy (for prosody diversity)
    "Wait.",
    "Really?",
    "Oh no.",
    "Are you serious?",
    "That's it.",
    "Interesting.",
    "Fine, okay.",
    "Whatever.",
    "Perfect.",
    "Absolutely not.",
    "I don't know.",
    "Maybe.",
    "Let me see.",
    "Try again.",
    "Come on.",
    "Stop it.",
    "Look at that.",
    "Here we go.",
    "Of course.",
    "Obviously.",

    # Technical and factual
    "The process involves three distinct stages of refinement.",
    "Each component must be calibrated independently before assembly.",
    "The algorithm processes the input data in parallel threads.",
    "Signal degradation occurs when the threshold exceeds the baseline.",
    "The results were statistically significant at the standard confidence level.",
    "Configuration parameters can be adjusted in the settings file.",
    "The system automatically retries failed connections after a timeout.",
    "Memory allocation is handled dynamically to optimize performance.",
    "Version two introduced several improvements to the core engine.",
    "The update should resolve the issue you've been experiencing.",

    # Storytelling and narrative
    "It all started on a perfectly ordinary Tuesday morning.",
    "She didn't know it yet, but everything was about to change.",
    "By the time anyone realized what had happened, it was too late.",
    "He stood at the crossroads, unsure which way to turn.",
    "The letter arrived three weeks after she had given up hope.",
    "No one in the village had seen anything like it before.",
    "It was the last place anyone expected to find the answer.",
    "They had been searching for years without any real leads.",
    "What happened next would stay with them for the rest of their lives.",
    "In the end, the truth was far simpler than anyone had imagined.",

    # Food, daily life, relatable
    "I could really go for some hot tea right about now.",
    "There's nothing better than a really good home-cooked meal.",
    "I always feel better after a long walk outside.",
    "Sleep deprivation really does mess with your thinking.",
    "I've been trying to cut back on caffeine lately.",
    "Nothing beats waking up on a Saturday with no plans.",
    "I keep starting books and forgetting to finish them.",
    "My phone battery is almost always dying at the worst moments.",
    "I can never remember where I put my keys.",
    "Somehow I always end up watching TV way too late.",

    # Reflective and philosophical
    "We spend so much time chasing things that don't really matter.",
    "Happiness is often less about circumstances and more about perspective.",
    "The people who push you the hardest usually care about you the most.",
    "Change is uncomfortable, but stagnation is its own kind of suffering.",
    "The version of yourself from five years ago would be surprised by who you are now.",
    "We often judge others by their actions, but ourselves by our intentions.",
    "Some lessons can only be learned through experience, not advice.",
    "The moments that define us are usually the ones we didn't see coming.",
    "It's easy to be kind when things are going well.",
    "What matters most is rarely what we spend the most time on.",

    # Humor and lightness
    "I have absolutely no idea what I'm doing, but here we go.",
    "That plan made perfect sense until I actually tried it.",
    "I said I'd be ready in five minutes, which was obviously a lie.",
    "My brain decided to stop working at the most inconvenient time.",
    "I was confident right up until the moment I wasn't.",
    "Turns out, confidence and competence are not the same thing.",
    "I spent three hours on that and it still looks terrible.",
    "My optimism is matched only by my complete lack of preparation.",
    "I told myself it would be easy. I was wrong. Very wrong.",
    "At some point you just have to laugh at the whole situation.",

    # More phoneme coverage
    "The rhythm of the music filled the quiet room with warmth.",
    "She carefully adjusted the intricate mechanism with precision.",
    "The thick fog rolled in across the fjord as evening fell.",
    "Crystalline structures form at specific temperature thresholds.",
    "His philosophy challenged the conventional wisdom of the era.",
    "The exhibition showcased extraordinary works from emerging artists.",
    "Mysterious circumstances surrounded the disappearance of the artifact.",
    "Extraordinary patience is required for this particular type of work.",
    "The combination of flavors was surprisingly sophisticated.",
    "Abstract thinking requires a different kind of mental flexibility.",
    "The phenomenon was observed repeatedly under controlled conditions.",
    "Genuine enthusiasm is contagious in the most wonderful way.",
    "The architectural details were incredibly intricate and beautiful.",
    "Simultaneous translation is one of the most demanding cognitive tasks.",
    "Perception shapes reality in ways we rarely fully appreciate.",

    # Additional variety
    "I keep thinking there must be a better way to do this.",
    "What if we tried approaching it from a completely different angle?",
    "The more I learn, the more I realize how much I don't know.",
    "It's weird how quickly you can adapt when you have no choice.",
    "I think we're overcomplicating something that's actually quite simple.",
    "There's more going on here than what's visible on the surface.",
    "We've been looking at this the wrong way from the beginning.",
    "Small consistent efforts compound into something much larger over time.",
    "The first attempt rarely goes the way you expect it to.",
    "Once you understand the fundamentals, everything else makes more sense.",
    "I've been wrong before, and I'll be wrong again, but not about this.",
    "The gap between knowing and doing is where most people get stuck.",
    "If you want different results, you have to try different things.",
    "It's not about being perfect, it's about being consistent.",
    "The real challenge isn't starting, it's maintaining momentum.",
    "Every problem contains the seeds of its own solution.",
    "The right timing can make all the difference in the world.",
    "Not everything that looks like an obstacle is actually one.",
    "Sometimes the answer is so obvious you almost miss it.",
    "The best decisions usually feel uncomfortable at first.",
]


def wait_ready(url: str, timeout: int = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/ready", timeout=5) as r:
                if r.status == 200:
                    return
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError(f"IndexTTS not ready at {url} after {timeout}s")


def synthesize(url: str, text: str) -> bytes:
    payload = json.dumps({
        "text": text,
        "request_id": "",
        "emotion": "neutral",
        "emotion_intensity": 50,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{url}/synthesize",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        body = json.loads(r.read())
    return base64.b64decode(body["wav_base64"])


def wav_duration(wav_bytes: bytes) -> float:
    with sf.SoundFile(io.BytesIO(wav_bytes)) as f:
        return f.frames / f.samplerate


def resample_to_22050(wav_bytes: bytes) -> bytes:
    """SBVITS2 expects 22050 Hz. Resample if IndexTTS outputs something else."""
    from scipy.signal import resample_poly
    from math import gcd
    audio, sr = sf.read(io.BytesIO(wav_bytes), dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    target_sr = 22050
    if sr != target_sr:
        g = gcd(sr, target_sr)
        audio = resample_poly(audio, target_sr // g, sr // g).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, audio, target_sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tts-url", default="http://127.0.0.1:9000")
    parser.add_argument("--target-hours", type=float, default=1.5)
    parser.add_argument("--output", default="data/sbvits2_training")
    parser.add_argument("--no-resample", action="store_true",
                        help="Skip resampling to 22050 Hz (use if IndexTTS already outputs 22050)")
    args = parser.parse_args()

    out_dir = Path(args.output)
    wavs_dir = out_dir / "wavs"
    wavs_dir.mkdir(parents=True, exist_ok=True)
    meta_path = out_dir / "metadata.csv"

    # Load existing metadata to resume
    existing: dict[str, str] = {}
    if meta_path.exists():
        with open(meta_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "|" in line:
                    name, text = line.split("|", 1)
                    existing[name] = text

    existing_duration = sum(
        wav_duration((wavs_dir / f"{k}.wav").read_bytes())
        for k in existing
        if (wavs_dir / f"{k}.wav").exists()
    )

    target_seconds = args.target_hours * 3600
    print(f"Target: {args.target_hours}h ({target_seconds:.0f}s)")
    print(f"Already collected: {existing_duration:.1f}s across {len(existing)} clips")

    print(f"Checking IndexTTS at {args.tts_url} ...")
    wait_ready(args.tts_url)
    print("IndexTTS is ready.")

    total_seconds = existing_duration
    clip_index = len(existing)

    sentences = SENTENCES.copy()
    loop = 0

    with open(meta_path, "a", encoding="utf-8") as meta_f:
        while total_seconds < target_seconds:
            if not sentences:
                loop += 1
                sentences = SENTENCES.copy()
                print(f"  [loop {loop}] cycling through sentences again")

            text = sentences.pop(0)
            name = f"koroki_{clip_index:05d}"
            wav_path = wavs_dir / f"{name}.wav"

            if name in existing:
                continue

            try:
                wav_bytes = synthesize(args.tts_url, text)
                if not args.no_resample:
                    wav_bytes = resample_to_22050(wav_bytes)
                duration = wav_duration(wav_bytes)
                wav_path.write_bytes(wav_bytes)
                meta_f.write(f"{name}|{text}\n")
                meta_f.flush()
                total_seconds += duration
                clip_index += 1
                if clip_index % 50 == 0:
                    print(f"  [{clip_index:5d}] {total_seconds/3600:.2f}h / {args.target_hours}h")
            except Exception as e:
                print(f"  WARN: failed on '{text[:40]}': {e}")

    print(f"\nDone. {clip_index} clips, {total_seconds/3600:.2f}h total → {out_dir}")
    print("Ready to feed into Style-Bert-VITS2 training.")


if __name__ == "__main__":
    main()
