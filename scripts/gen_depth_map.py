"""Generate a depth map for a room painting (frontend compositor step 2).

Depth-Anything V2 Small via transformers (CPU, one-time per painting, ~100MB
model download on first run). The grayscale map drives the stage's
DisplacementFilter: bright = near (moves with parallax), dark = far (still).

Usage (main .venv):
    .venv\\Scripts\\python.exe scripts\\gen_depth_map.py clients\\stage\\assets\\room.png
    -> writes room_depth.png beside the input
"""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "clients/stage/assets/room.png")
    if not src.exists():
        raise SystemExit(f"input not found: {src}")
    out = src.with_name(src.stem + "_depth.png")

    from PIL import Image
    from transformers import pipeline

    print(f"loading Depth-Anything V2 Small (CPU)...")
    pipe = pipeline("depth-estimation", model="depth-anything/Depth-Anything-V2-Small-hf",
                    device=-1)
    img = Image.open(src).convert("RGB")
    print(f"estimating depth for {src.name} ({img.size[0]}x{img.size[1]})...")
    depth = pipe(img)["depth"]  # PIL grayscale, bright = near
    depth = depth.resize(img.size)
    depth.save(out)
    print(f"depth map -> {out}")


if __name__ == "__main__":
    main()
