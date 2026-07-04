"""Expand the spk_embed weight in a DiffSinger checkpoint from N speakers to N+1.

Usage:
    python expand_spk_embed_v5.py \
        --ckpt checkpoints/koroki_multispk_v4/model_ckpt_steps_60000.ckpt \
        --out  checkpoints/koroki_multispk_v4_expanded/model_ckpt_steps_60000.ckpt \
        --new-spk lisa

The new speaker row is zero-initialised so the model starts from silence and
learns the voice purely from the training data rather than copying an existing
speaker embedding.  All other weights are unchanged.
"""

import argparse
import shutil
from pathlib import Path

import torch


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ckpt", required=True, help="Source checkpoint path")
    parser.add_argument("--out",  required=True, help="Output checkpoint path")
    parser.add_argument("--new-spk", default="lisa", help="Name of the new speaker (informational only)")
    args = parser.parse_args()

    src = Path(args.ckpt)
    dst = Path(args.out)
    dst.parent.mkdir(parents=True, exist_ok=True)

    print(f"Loading {src} ...")
    ckpt = torch.load(str(src), map_location="cpu")

    state = ckpt.get("state_dict", ckpt)

    # Find the spk_embed key — DiffSinger stores it as model.fs2.spk_embed.weight
    # or similar.  Print all embedding-shaped tensors to help diagnose.
    spk_key = None
    for k, v in state.items():
        if "spk_embed" in k and v.ndim == 2:
            print(f"  Found spk_embed candidate: {k}  shape={list(v.shape)}")
            spk_key = k

    if spk_key is None:
        raise RuntimeError(
            "Could not find a spk_embed tensor in the checkpoint. "
            "Run with --ckpt and inspect the printed keys manually."
        )

    old = state[spk_key]  # [N, dim]
    n_spk, dim = old.shape
    new_row = torch.zeros(1, dim, dtype=old.dtype)
    expanded = torch.cat([old, new_row], dim=0)  # [N+1, dim]
    state[spk_key] = expanded

    print(f"  Expanded {spk_key}: {list(old.shape)} → {list(expanded.shape)}")
    print(f"  New speaker '{args.new_spk}' gets spk_id={n_spk} (zero-init)")

    if "state_dict" in ckpt:
        ckpt["state_dict"] = state
    else:
        ckpt = state

    print(f"Saving → {dst}")
    torch.save(ckpt, str(dst))
    print("Done.")


if __name__ == "__main__":
    main()
