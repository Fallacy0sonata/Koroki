"""Corpus profiler — "which game should she debut on?", answered from data.

Koroki plays vision-only at seconds-per-decision (docs/game_limbs_verdict_
2026-07-09.md). Not every game suits that. This samples the USABLE segments of
the banked corpus (quality.jsonl from footage_filter) and measures, per game,
the three things that decide vision-only tractability:

  - text_density / ocr_conf : can she READ the screen? Her whole grounding —
    blackboard, motor-planner targets — rides on OCR. A game with big legible
    UI text is tractable; a game that's all 3D world and no words is hostile.
  - motion_rate : how fast does the screen change? A calm, turn-based-ish game
    fits a ~1s decision loop; a twitch platformer does not.

-> a tractability score ranks the 7 games by how suited each is to how she
actually plays. Byproduct: the real on-screen VOCABULARY per game (top OCR
strings), which both verifies the onboarding cards against reality and gives
the motor planner a head start on legal click targets.

Uses RapidOCR directly (CPU, ~300ms/frame) — no service needed. Output:
`<game>/profile.json` next to the videos + a printed ranking.

Usage:
  .venv\\Scripts\\python.exe experiments\\limbs\\corpus_profiler.py --samples 25
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path

CORPUS_ROOT = Path(r"G:\My Drive\Koroki Storage\datasets\limbs_youtube")


def usable_segments(quality_path: Path) -> dict[str, list[tuple[float, float]]]:
    """video_id -> list of (start, end) usable spans, from footage_filter output."""
    out: dict[str, list[tuple[float, float]]] = {}
    if not quality_path.exists():
        return out
    for line in quality_path.read_text(encoding="utf-8").splitlines():
        try:
            rep = json.loads(line)
        except Exception:
            continue
        if rep.get("montage"):
            continue
        spans = [(s["start"], s["end"]) for s in rep.get("segments", []) if s["kind"] == "usable"]
        if spans:
            out[rep["id"]] = spans
    return out


def sample_timestamps(spans: list[tuple[float, float]], n: int) -> list[float]:
    """Uniform-ish sample of timestamps weighted by span length."""
    total = sum(e - s for s, e in spans)
    if total <= 0:
        return []
    picks = []
    for _ in range(n):
        r = random.uniform(0, total)
        acc = 0.0
        for s, e in spans:
            if acc + (e - s) >= r:
                picks.append(s + (r - acc))
                break
            acc += e - s
    return sorted(picks)


def profile_game(folder: Path, ocr, samples: int) -> dict | None:
    import cv2
    import numpy as np

    seg_map = usable_segments(folder / "quality.jsonl")
    if not seg_map:
        return None
    # distribute the sample budget across videos proportional to usable time
    per_video = max(3, samples // max(1, len(seg_map)))
    text_counts, confs, motions = [], [], []
    vocab: Counter = Counter()
    prev_gray = None
    frames_read = 0

    for vid, spans in seg_map.items():
        vpath = next(folder.glob(f"{vid}.*"), None)
        if vpath is None or vpath.suffix == ".jsonl":
            continue
        cap = cv2.VideoCapture(str(vpath))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        for ts in sample_timestamps(spans, per_video):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(ts * fps))
            ok, frame = cap.read()
            if not ok:
                continue
            frames_read += 1
            result, _ = ocr(frame)
            boxes = result or []
            text_counts.append(len(boxes))
            for _box, text, conf in boxes:
                confs.append(float(conf))
                t = str(text).strip().lower()
                if 2 <= len(t) <= 24 and not t.isdigit():
                    vocab[t] += 1
            small = cv2.cvtColor(cv2.resize(frame, (320, 180)), cv2.COLOR_BGR2GRAY)
            if prev_gray is not None:
                motions.append(float(cv2.absdiff(small, prev_gray).mean()) / 255.0)
            prev_gray = small
        cap.release()

    if frames_read == 0:
        return None
    text_density = float(np.mean(text_counts))
    ocr_conf = float(np.mean(confs)) if confs else 0.0
    motion_rate = float(np.mean(motions)) if motions else 0.0

    # tractability heuristic (first pass, owner-tunable): readable + calm = good
    text_score = min(1.0, text_density / 8.0)
    calm_score = 1.0 - min(1.0, motion_rate / 0.30)
    tractability = round(0.4 * text_score + 0.3 * ocr_conf + 0.3 * calm_score, 3)

    return {
        "game": folder.name,
        "frames_profiled": frames_read,
        "text_density": round(text_density, 1),
        "ocr_conf": round(ocr_conf, 3),
        "motion_rate": round(motion_rate, 3),
        "tractability": tractability,
        "top_vocab": vocab.most_common(20),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Profile the corpus for vision-only tractability.")
    ap.add_argument("--samples", type=int, default=25, help="frames per game")
    ap.add_argument("--root", default=str(CORPUS_ROOT))
    args = ap.parse_args()

    from rapidocr_onnxruntime import RapidOCR

    ocr = RapidOCR()
    root = Path(args.root)
    profiles = []
    for folder in sorted(root.iterdir()):
        if not folder.is_dir():
            continue
        print(f"[profile] {folder.name} ...", flush=True)
        prof = profile_game(folder, ocr, args.samples)
        if prof is None:
            print(f"  (no usable segments — skipped)")
            continue
        (folder / "profile.json").write_text(json.dumps(prof, indent=1), encoding="utf-8")
        profiles.append(prof)
        print(f"  text={prof['text_density']} conf={prof['ocr_conf']} "
              f"motion={prof['motion_rate']} -> tractability {prof['tractability']}")

    if profiles:
        print("\n=== VISION-ONLY TRACTABILITY RANKING (debut-game guide) ===")
        for p in sorted(profiles, key=lambda x: x["tractability"], reverse=True):
            top = ", ".join(t for t, _ in p["top_vocab"][:6])
            print(f"  {p['tractability']:.3f}  {p['game']:<20} "
                  f"(text {p['text_density']}, calm {1 - min(1, p['motion_rate'] / 0.3):.2f}) "
                  f"| UI: {top}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
