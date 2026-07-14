"""The Inverse Dynamics Model network (LIMBS Stage 1).

A frame-stack CNN: the non-causal window of grayscale frames is stacked on the
channel axis and read by a Nature/Impala-style conv tower, then split into three
heads — keys (multi-label), mouse buttons (multi-label), camera (2 regressed
deltas). Deliberately small (~2-3M params): the IDM is a per-game labeler that
must train in an hour on a 3090, not a foundation model.

Losses combine as: BCE(keys) + BCE(buttons) + smooth_l1(camera). The caller
weights them; defaults are 1:1:1 which the synthetic smoke test confirms learns
all three heads together.
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

from .config import FRAME_SIZE, N_BUTTONS, N_KEYS, STACK


class IDM(nn.Module):
    def __init__(self, stack: int = STACK, n_keys: int = N_KEYS,
                 n_buttons: int = N_BUTTONS, width: int = 32):
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(stack, width, kernel_size=8, stride=4), nn.ReLU(inplace=True),
            nn.Conv2d(width, width * 2, kernel_size=4, stride=2), nn.ReLU(inplace=True),
            nn.Conv2d(width * 2, width * 2, kernel_size=3, stride=1), nn.ReLU(inplace=True),
        )
        with torch.no_grad():
            dummy = torch.zeros(1, stack, FRAME_SIZE, FRAME_SIZE)
            flat = self.conv(dummy).flatten(1).shape[1]
        self.trunk = nn.Sequential(nn.Linear(flat, 512), nn.ReLU(inplace=True))
        self.head_keys = nn.Linear(512, n_keys)      # logits (BCE)
        self.head_buttons = nn.Linear(512, n_buttons)  # logits (BCE)
        self.head_camera = nn.Linear(512, 2)          # normalized dx, dy

    def forward(self, x: torch.Tensor) -> dict:
        """x: (B, STACK, H, W) grayscale in [0,1] -> head dict."""
        h = self.trunk(self.conv(x).flatten(1))
        return {
            "keys": self.head_keys(h),
            "buttons": self.head_buttons(h),
            "camera": self.head_camera(h),
        }

    def loss(self, out: dict, target: dict,
             w_keys: float = 1.0, w_buttons: float = 1.0, w_camera: float = 1.0) -> dict:
        lk = F.binary_cross_entropy_with_logits(out["keys"], target["keys"])
        lb = F.binary_cross_entropy_with_logits(out["buttons"], target["buttons"])
        lc = F.smooth_l1_loss(out["camera"], target["camera"])
        total = w_keys * lk + w_buttons * lb + w_camera * lc
        return {"total": total, "keys": lk, "buttons": lb, "camera": lc}

    @torch.no_grad()
    def predict(self, x: torch.Tensor, key_thresh: float = 0.5) -> dict:
        """Inference -> discrete action labels (for pseudo-labeling YouTube)."""
        out = self.forward(x)
        return {
            "keys": (torch.sigmoid(out["keys"]) >= key_thresh),
            "buttons": (torch.sigmoid(out["buttons"]) >= key_thresh),
            "camera": out["camera"],
        }


def param_count(model: nn.Module) -> int:
    return sum(p.numel() for p in model.parameters())
