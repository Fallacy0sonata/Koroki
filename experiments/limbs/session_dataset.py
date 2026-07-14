"""Recorder session → aligned (frame, action) pairs — IDM dataset stage
(LIMBS Stage 1 prep, 2026-07-09).

Consumes what demo_recorder.py writes (video.mp4 + frames.jsonl + inputs.jsonl
+ session.json) and produces the exact supervision format the inverse-dynamics
model trains on: for every captured frame, what the hands were doing during
that frame's interval. Proving this pipeline NOW — before the owner banks
50 hours — is the point: a format bug found at training time would cost the
whole corpus.

Per-frame action bin:
  keys_down   — keys held at any point in the interval (kd/ku state machine)
  clicks      — [(button, x, y), ...] presses inside the interval
  move_dx/dy  — RAW mouse deltas summed (the camera signal; survives
                pointer lock, which cursor positions do not)
  cursor      — last known cursor position (menu/UI signal)
  scroll      — net scroll amount

Usage:
  .venv\\Scripts\\python.exe experiments\\limbs\\session_dataset.py --session data\\demo_recordings\\<name>
  # --export out.jsonl writes the aligned pairs; default prints stats only
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def load_session(session_dir: Path) -> tuple[dict, list[dict], list[dict]]:
    manifest = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
    frames = [json.loads(l) for l in
              (session_dir / "frames.jsonl").read_text(encoding="utf-8").splitlines()]
    events = [json.loads(l) for l in
              (session_dir / "inputs.jsonl").read_text(encoding="utf-8").splitlines()]
    return manifest, frames, events


def bin_events(frames: list[dict], events: list[dict]) -> list[dict]:
    """Assign every input event to the frame interval it happened in.

    Frame i's bin covers [t_i, t_{i+1}); the last frame gets a same-width
    interval. Events are already time-ordered (single writer thread). Keys
    held across a focus-pause are force-released — the recorder logged no
    inputs during the pause, so carrying "down" state through it would
    fabricate holds that never happened on the game.
    """
    if not frames:
        return []
    bins: list[dict] = [
        {"i": f["i"], "t": f["t"], "keys_down": set(), "clicks": [],
         "move_dx": 0, "move_dy": 0, "cursor": None, "scroll": 0}
        for f in frames
    ]
    # interval end for each frame
    ends = [frames[k + 1]["t"] for k in range(len(frames) - 1)]
    ends.append(frames[-1]["t"] + (ends[-1] - frames[-2]["t"] if len(frames) > 1 else 0.1))

    held: set[str] = set()
    fi = 0
    for ev in events:
        t = ev["t"]
        if t < bins[0]["t"]:
            # pre-first-frame events still shape the held-keys state
            if ev["e"] == "kd":
                held.add(ev["k"])
            elif ev["e"] == "ku":
                held.discard(ev["k"])
            continue
        while fi < len(bins) - 1 and t >= ends[fi]:
            fi += 1
            bins[fi]["keys_down"] |= held  # carry held keys into the new interval
        b = bins[fi]
        kind = ev["e"]
        if kind == "kd":
            held.add(ev["k"])
            b["keys_down"].add(ev["k"])
        elif kind == "ku":
            held.discard(ev["k"])
        elif kind == "md":
            b["clicks"].append((ev.get("b", "left"), ev.get("x"), ev.get("y")))
        elif kind == "mr":
            b["move_dx"] += ev.get("dx", 0)
            b["move_dy"] += ev.get("dy", 0)
        elif kind == "mm":
            b["cursor"] = (ev.get("x"), ev.get("y"))
        elif kind == "sc":
            b["scroll"] += ev.get("dy", 0)
        elif kind == "pause":
            held.clear()  # never fabricate holds across an unfocused gap
    # initial holds for frame 0 were merged as events streamed; freeze sets
    for b in bins:
        b["keys_down"] = sorted(b["keys_down"])
    return bins


def session_stats(bins: list[dict]) -> dict:
    """Sanity numbers: does the recording contain what the owner actually did?"""
    if not bins:
        return {"frames": 0}
    key_frames = sum(1 for b in bins if b["keys_down"])
    move_frames = sum(1 for b in bins if b["move_dx"] or b["move_dy"])
    idle_frames = sum(1 for b in bins if not b["keys_down"] and not b["clicks"]
                      and not (b["move_dx"] or b["move_dy"]))
    key_hist = Counter(k for b in bins for k in b["keys_down"])
    duration = bins[-1]["t"] - bins[0]["t"] if len(bins) > 1 else 0.0
    clicks = sum(len(b["clicks"]) for b in bins)
    return {
        "frames": len(bins),
        "duration_s": round(duration, 1),
        "key_frame_ratio": round(key_frames / len(bins), 3),
        "move_frame_ratio": round(move_frames / len(bins), 3),
        "idle_frame_ratio": round(idle_frames / len(bins), 3),
        "clicks_total": clicks,
        "clicks_per_min": round(clicks / (duration / 60.0), 1) if duration else 0.0,
        "top_keys": key_hist.most_common(8),
        "total_move_px": sum(abs(b["move_dx"]) + abs(b["move_dy"]) for b in bins),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Recorder session -> IDM-ready frame/action pairs.")
    ap.add_argument("--session", required=True, help="session directory")
    ap.add_argument("--export", default=None, help="write aligned pairs to this .jsonl")
    args = ap.parse_args()

    sdir = Path(args.session)
    manifest, frames, events = load_session(sdir)
    bins = bin_events(frames, events)
    stats = session_stats(bins)
    print(f"[dataset] {sdir.name}: game={manifest.get('game')} "
          f"rect={manifest.get('rect', {}).get('width')}x{manifest.get('rect', {}).get('height')}")
    print(json.dumps(stats, indent=1))

    if args.export:
        out = Path(args.export)
        with open(out, "w", encoding="utf-8") as f:
            for b in bins:
                f.write(json.dumps(b, separators=(",", ":")) + "\n")
        print(f"[dataset] exported {len(bins)} aligned pairs -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
