"""
Expand spk_embed in a DiffSinger checkpoint from 2 speakers to 3.
Preserves Koroki (row 0) and YOASOBI (row 1), adds random init row for ado (row 2).

Run before training koroki_multispk_v4.
"""
import pathlib
import torch

SRC = pathlib.Path(
    r"C:\Users\Shinn\Desktop\Koroki\experiments\diffsinger\DiffSinger"
    r"\checkpoints\koroki_multispk_v3\model_ckpt_steps_40000.ckpt"
)
DST_DIR = pathlib.Path(
    r"C:\Users\Shinn\Desktop\Koroki\experiments\diffsinger\DiffSinger"
    r"\checkpoints\koroki_multispk_v3_expanded"
)
DST = DST_DIR / SRC.name

DST_DIR.mkdir(exist_ok=True)

print(f"Loading {SRC} ...")
ckpt = torch.load(SRC, map_location="cpu")
sd = ckpt["state_dict"]

# Find the spk_embed key — could be .weight for nn.Embedding or bare for custom class
spk_key = None
for k in sd:
    if "spk_embed" in k:
        print(f"  Found key: {k}  shape: {sd[k].shape}")
        spk_key = k

if spk_key is None:
    raise KeyError("spk_embed not found in checkpoint — check key names above")

old = sd[spk_key]  # [2, hidden] or [2, hidden] depending on embedding type
if old.ndim != 2:
    raise ValueError(f"Unexpected spk_embed shape {old.shape}, expected 2D")

print(f"Expanding spk_embed: {list(old.shape)} → [{old.shape[0] + 1}, {old.shape[1]}]")

# Small random init for ado — std 0.01 so it doesn't destabilise early training
new_row = torch.randn(1, old.shape[1]) * 0.01
new_embed = torch.cat([old, new_row], dim=0)
sd[spk_key] = new_embed

torch.save(ckpt, DST)
print(f"Saved expanded checkpoint → {DST}")
print("Speakers in new checkpoint:")
print("  0 = koroki  (preserved)")
print("  1 = yoasobi (preserved)")
print("  2 = ado     (random init, will learn during v4 training)")
