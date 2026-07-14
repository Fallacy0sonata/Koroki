"""Real recorder-session dataset for the IDM (LIMBS Stage 1).

Turns the owner's demo_recorder sessions into (frame_stack, action) training
pairs — the SAME action contract as synthetic.py, so a model that learned the
synthetic proxy trains on real data with zero head changes. Reuses
session_dataset.bin_events (validated lossless 2026-07-09) for the input side;
adds the video-frame side.

encode_action() is pure and unit-tested. Frame reads use cv2 seeking, which is
correct but slow for random access — the 3090 run should pre-decode sampled
frames to an .npy cache first (noted in README; not needed to prove the path).
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import numpy as np
import torch

_LIMBS = Path(__file__).resolve().parents[1]
if str(_LIMBS) not in sys.path:
    sys.path.insert(0, str(_LIMBS))
from session_dataset import bin_events, load_session  # noqa: E402

from .config import (  # noqa: E402
    BUTTON_INDEX, CAMERA_SCALE, FRAME_SIZE, FRAMES_AFTER, FRAMES_BEFORE,
    KEY_INDEX, N_BUTTONS, N_KEYS, STACK,
)


def encode_action(bin_: dict) -> dict:
    """One aligned bin -> IDM target vectors (numpy). Pure; unit-tested.

    Keys not in KEY_VOCAB are ignored (game-irrelevant). Buttons come from
    click presses in the interval (holds need button up/down tracking in the
    recorder — known limitation, noted). Camera = raw deltas / CAMERA_SCALE,
    clamped to the trained [-1, 1] range."""
    keys = np.zeros(N_KEYS, dtype="float32")
    for k in bin_.get("keys_down", []):
        i = KEY_INDEX.get(k)
        if i is not None:
            keys[i] = 1.0
    buttons = np.zeros(N_BUTTONS, dtype="float32")
    for b, *_ in bin_.get("clicks", []):
        j = BUTTON_INDEX.get(b)
        if j is not None:
            buttons[j] = 1.0
    cam = np.array([bin_.get("move_dx", 0), bin_.get("move_dy", 0)], dtype="float32")
    cam = np.clip(cam / CAMERA_SCALE, -1.0, 1.0)
    return {"keys": keys, "buttons": buttons, "camera": cam}


def _to_gray_square(frame: np.ndarray) -> np.ndarray:
    import cv2

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return (cv2.resize(gray, (FRAME_SIZE, FRAME_SIZE)).astype("float32") / 255.0)


class Session:
    """One recorder session: index of target frames + their action targets."""

    def __init__(self, session_dir: Path):
        import cv2

        self.dir = session_dir
        self.manifest, frames, events = load_session(session_dir)
        self.bins = bin_events(frames, events)
        self.video = session_dir / "video.mp4"
        self._cap = cv2.VideoCapture(str(self.video)) if self.video.exists() else None
        # a frame index is a valid TARGET only if its whole stack window exists
        self.targets = list(range(FRAMES_BEFORE, len(self.bins) - FRAMES_AFTER))

    def __len__(self) -> int:
        return len(self.targets) if self._cap is not None else 0

    def _read_stack(self, center: int) -> np.ndarray | None:
        import cv2

        out = np.zeros((STACK, FRAME_SIZE, FRAME_SIZE), dtype="float32")
        for si, fi in enumerate(range(center - FRAMES_BEFORE, center + FRAMES_AFTER + 1)):
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
            ok, frame = self._cap.read()
            if not ok:
                return None
            out[si] = _to_gray_square(frame)
        return out

    def get(self, center: int) -> tuple[np.ndarray, dict] | None:
        stack = self._read_stack(center)
        if stack is None:
            return None
        return stack, encode_action(self.bins[center])


class SessionCorpus:
    """All sessions under a root, sampled uniformly over target frames."""

    def __init__(self, root: Path):
        self.sessions: list[Session] = []
        for d in sorted(Path(root).glob("*")):
            if (d / "session.json").exists():
                try:
                    s = Session(d)
                    if len(s) > 0:
                        self.sessions.append(s)
                except Exception as exc:
                    print(f"[idm-data] skip {d.name}: {exc}")
        self._index = [(si, fi) for si, s in enumerate(self.sessions) for fi in s.targets]

    def __len__(self) -> int:
        return len(self._index)

    def sample_batch(self, batch: int) -> tuple[torch.Tensor, dict]:
        xs, keys, buttons, cams = [], [], [], []
        while len(xs) < batch:
            si, fi = random.choice(self._index)
            got = self.sessions[si].get(fi)
            if got is None:
                continue
            stack, target = got
            xs.append(stack)
            keys.append(target["keys"]); buttons.append(target["buttons"]); cams.append(target["camera"])
        return (
            torch.from_numpy(np.stack(xs)),
            {"keys": torch.from_numpy(np.stack(keys)),
             "buttons": torch.from_numpy(np.stack(buttons)),
             "camera": torch.from_numpy(np.stack(cams))},
        )
