"""Segment-level footage quality filter — the corpus hard gate (LIMBS, 2026-07-09).

Title filters at harvest time are best-effort; THIS is the honest gate before
anything trains (docs/game_limbs_verdict_2026-07-09.md, contamination lesson).
Per video it detects, with pure OpenCV on sampled frames:

  - HARD CUTS: frame-to-frame structural jumps. Edited/montage footage is the
    worst corpus poison — a jump cut teaches the model teleportation. Videos
    with a high cut rate are flagged unusable outright.
  - STATIC STRETCHES: near-zero motion for long spans (AFK, idle menus).
    Dropped as segments; they teach nothing but doing nothing.
  - USABLE SEGMENTS: continuous spans >= min_segment_s between cuts with real
    motion. The usable_ratio (usable seconds / duration) is the ranking metric
    for assembling the behavior-clone training set later.

Output: `quality.jsonl` next to the videos (one line per video, segments
included) + a console table. Rerun-safe: already-analyzed ids are skipped.

Usage (main .venv, needs only cv2+numpy already present):
  .venv\\Scripts\\python.exe experiments\\limbs\\footage_filter.py --dir "G:\\My Drive\\Koroki Storage\\datasets\\limbs_youtube\\sols_rng"
  # or --video path\\to\\one.mp4 for a single file
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# tunables — calibrated on the first banked Sol's RNG batch (2026-07-09)
SAMPLE_FPS = 3.0          # analysis rate; 3 fps catches cuts and idle fine
DOWNSCALE_W = 320         # analysis resolution
CUT_DIFF = 42.0           # mean-abs-diff (0-255) above this = cut CANDIDATE
# Two-stage cut detection (2026-07-10): fast camera whips (Tower of Hell)
# shift every pixel but keep the SCENE — histogram stays correlated. A real
# cut changes both. Candidate becomes a cut only below this correlation.
CUT_HIST_CORR = 0.70
STATIC_DIFF = 1.2         # below this = static frame
STATIC_MIN_S = 12.0       # static span longer than this = dead segment
MIN_SEGMENT_S = 8.0       # usable spans must be at least this long
MAX_CUTS_PER_MIN = 6.0    # above this the video is montage — unusable


def analyze_video(path: Path) -> dict | None:
    """Sample frames, classify inter-frame motion, build segments."""
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return None
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = total / fps if fps else 0.0
    step = max(1, int(round(fps / SAMPLE_FPS)))

    prev = None
    prev_hist = None
    events: list[tuple[float, str]] = []  # (t, "cut"|"static"|"motion")
    idx = 0
    while True:
        ok = cap.grab()  # grab-without-decode keeps skipping cheap
        if not ok:
            break
        if idx % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            h = int(frame.shape[0] * DOWNSCALE_W / frame.shape[1])
            small = cv2.cvtColor(cv2.resize(frame, (DOWNSCALE_W, h)), cv2.COLOR_BGR2GRAY)
            hist = cv2.calcHist([small], [0], None, [64], [0, 256])
            cv2.normalize(hist, hist)
            t = idx / fps
            if prev is not None and prev.shape == small.shape:
                diff = float(cv2.absdiff(small, prev).mean())
                if diff >= CUT_DIFF:
                    corr = float(cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL))
                    # whip pan: pixels jump, scene histogram survives -> motion
                    events.append((t, "cut" if corr <= CUT_HIST_CORR else "motion"))
                elif diff <= STATIC_DIFF:
                    events.append((t, "static"))
                else:
                    events.append((t, "motion"))
            prev = small
            prev_hist = hist
        idx += 1
    cap.release()
    if not events:
        return None

    # segments: split at cuts; inside each, collapse static runs
    cuts = [t for t, kind in events if kind == "cut"]
    segments: list[dict] = []
    seg_start = 0.0
    bounds = cuts + [duration]
    ei = 0
    for bound in bounds:
        # static runs inside [seg_start, bound)
        run_start = None
        usable_from = seg_start
        while ei < len(events) and events[ei][0] < bound:
            t, kind = events[ei]
            if kind == "static":
                if run_start is None:
                    run_start = t
            else:
                if run_start is not None and t - run_start >= STATIC_MIN_S:
                    # close the usable span before the static run
                    if run_start - usable_from >= MIN_SEGMENT_S:
                        segments.append({"start": round(usable_from, 1),
                                         "end": round(run_start, 1), "kind": "usable"})
                    segments.append({"start": round(run_start, 1),
                                     "end": round(t, 1), "kind": "static"})
                    usable_from = t
                run_start = None
            ei += 1
        end = bound
        if run_start is not None and end - run_start >= STATIC_MIN_S:
            if run_start - usable_from >= MIN_SEGMENT_S:
                segments.append({"start": round(usable_from, 1),
                                 "end": round(run_start, 1), "kind": "usable"})
            segments.append({"start": round(run_start, 1), "end": round(end, 1),
                             "kind": "static"})
        elif end - usable_from >= MIN_SEGMENT_S:
            segments.append({"start": round(usable_from, 1), "end": round(end, 1),
                             "kind": "usable"})
        seg_start = bound
        usable_from = bound

    usable_s = sum(s["end"] - s["start"] for s in segments if s["kind"] == "usable")
    cuts_per_min = len(cuts) / (duration / 60.0) if duration else 0.0
    montage = cuts_per_min > MAX_CUTS_PER_MIN
    return {
        "duration_s": round(duration, 1),
        "cuts": len(cuts),
        "cuts_per_min": round(cuts_per_min, 2),
        "montage": montage,
        "usable_s": round(usable_s, 1),
        "usable_ratio": round(usable_s / duration, 3) if duration else 0.0,
        "segments": segments if not montage else [],  # montage: nothing survives
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Segment-level gameplay footage quality gate.")
    ap.add_argument("--dir", default=None, help="folder of banked .mp4s (writes quality.jsonl there)")
    ap.add_argument("--video", default=None, help="single video file")
    ap.add_argument("--redo", action="store_true", help="re-analyze already-reported videos")
    args = ap.parse_args()
    if not args.dir and not args.video:
        ap.error("--dir or --video required")

    if args.video:
        rep = analyze_video(Path(args.video))
        print(json.dumps(rep, indent=1))
        return 0

    folder = Path(args.dir)
    videos = sorted(folder.glob("*.mp4"))
    if not videos:
        print(f"[filter] no mp4s in {folder}")
        return 1
    report_path = folder / "quality.jsonl"
    done: set[str] = set()
    if report_path.exists() and not args.redo:
        for line in report_path.read_text(encoding="utf-8").splitlines():
            try:
                done.add(json.loads(line)["id"])
            except Exception:
                continue

    print(f"[filter] {folder.name}: {len(videos)} videos ({len(done)} already reported)")
    # --redo rewrites the report — appending would leave two verdicts per id
    with open(report_path, "w" if args.redo else "a", encoding="utf-8") as rf:
        for v in videos:
            if v.stem in done:
                continue
            t0 = time.perf_counter()
            rep = analyze_video(v)
            if rep is None:
                print(f"  {v.stem}: UNREADABLE")
                continue
            rep = {"id": v.stem, **rep, "analyzed_s": round(time.perf_counter() - t0, 1)}
            rf.write(json.dumps(rep, separators=(",", ":")) + "\n")
            rf.flush()
            tag = "MONTAGE-REJECT" if rep["montage"] else f"usable {rep['usable_ratio']:.0%}"
            print(f"  {v.stem}: {rep['duration_s'] / 60:.0f}min, {rep['cuts']} cuts "
                  f"({rep['cuts_per_min']:.1f}/min), {tag} [{rep['analyzed_s']}s]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
