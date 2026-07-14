"""Pre-decode recorder sessions to .npz shards for fast IDM training.

data.Session seeks the mp4 per frame — correct, but random access during a
20k-step run is brutally slow. This decodes every frame ONCE (sequential, fast),
stores grayscale frames as uint8 + the aligned action targets, so training reads
array slices from RAM/mmap instead of seeking video. A target's frame stack is
just a slice of the frame array (adjacent targets share frames — no 4x
duplication). Shards go to G: (owner's sandbox), regenerable.

  precache: .venv\\Scripts\\python.exe -m experiments.limbs.idm.precache --sessions data/demo_recordings
  train:    ...idm.train --sessions <cache_dir>   (CachedCorpus auto-detects .npz)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

_LIMBS = Path(__file__).resolve().parents[1]
if str(_LIMBS) not in sys.path:
    sys.path.insert(0, str(_LIMBS))
from session_dataset import bin_events, load_session  # noqa: E402

from .config import FRAME_SIZE, FRAMES_AFTER, FRAMES_BEFORE, N_BUTTONS, N_KEYS, STACK  # noqa: E402
from .data import encode_action  # noqa: E402

CACHE_ROOT = Path(r"G:\My Drive\Koroki Storage\datasets\limbs_idm_cache")


def precache_session(session_dir: Path, out_dir: Path) -> Path | None:
    """Decode one session -> a compressed .npz shard. None if unreadable."""
    import cv2

    video = session_dir / "video.mp4"
    if not video.exists():
        return None
    manifest, frames_meta, events = load_session(session_dir)
    bins = bin_events(frames_meta, events)
    n = len(bins)
    if n <= STACK:
        return None

    frames = np.zeros((n, FRAME_SIZE, FRAME_SIZE), dtype=np.uint8)
    cap = cv2.VideoCapture(str(video))
    read = 0
    while read < n:
        ok, frame = cap.read()
        if not ok:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        frames[read] = cv2.resize(gray, (FRAME_SIZE, FRAME_SIZE))
        read += 1
    cap.release()
    if read <= STACK:
        return None

    frames = frames[:read]
    keys = np.zeros((read, N_KEYS), dtype=np.float32)
    buttons = np.zeros((read, N_BUTTONS), dtype=np.float32)
    camera = np.zeros((read, 2), dtype=np.float32)
    for i in range(read):
        t = encode_action(bins[i])
        keys[i], buttons[i], camera[i] = t["keys"], t["buttons"], t["camera"]
    valid = np.arange(FRAMES_BEFORE, read - FRAMES_AFTER, dtype=np.int64)

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{session_dir.name}.npz"
    np.savez_compressed(out, frames=frames, keys=keys, buttons=buttons,
                        camera=camera, valid=valid)
    return out


class CachedCorpus:
    """IDM training source over precached .npz shards. Same sample_batch contract
    as data.SessionCorpus, but reads array slices — no video seeking."""

    def __init__(self, cache_dir: Path):
        import random

        self._random = random
        self.shards = []
        for f in sorted(Path(cache_dir).glob("*.npz")):
            self.shards.append(np.load(f))
        # (shard, center) index over every valid target in every shard
        self._index = [(si, int(c)) for si, d in enumerate(self.shards) for c in d["valid"]]

    def __len__(self) -> int:
        return len(self._index)

    def _stack(self, shard, center: int) -> np.ndarray:
        # slice the shared frame array -> (STACK, H, W) float32 in [0,1]
        window = shard["frames"][center - FRAMES_BEFORE: center + FRAMES_AFTER + 1]
        return window.astype("float32") / 255.0

    def sample_batch(self, batch: int):
        import torch

        xs, keys, buttons, cams = [], [], [], []
        for _ in range(batch):
            si, c = self._random.choice(self._index)
            d = self.shards[si]
            xs.append(self._stack(d, c))
            keys.append(d["keys"][c]); buttons.append(d["buttons"][c]); cams.append(d["camera"][c])
        return (
            torch.from_numpy(np.stack(xs)),
            {"keys": torch.from_numpy(np.stack(keys)),
             "buttons": torch.from_numpy(np.stack(buttons)),
             "camera": torch.from_numpy(np.stack(cams))},
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="Pre-decode recorder sessions for fast IDM training.")
    ap.add_argument("--sessions", required=True, help="recorder-session root")
    ap.add_argument("--out", default=str(CACHE_ROOT))
    args = ap.parse_args()

    out_dir = Path(args.out)
    root = Path(args.sessions)
    n = 0
    for d in sorted(root.glob("*")):
        if (d / "session.json").exists():
            shard = precache_session(d, out_dir)
            if shard:
                size_mb = shard.stat().st_size / 1e6
                print(f"[precache] {d.name} -> {shard.name} ({size_mb:.1f} MB)")
                n += 1
    print(f"[precache] {n} sessions -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
