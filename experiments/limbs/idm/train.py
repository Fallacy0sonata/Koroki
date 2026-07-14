"""IDM training loop (LIMBS Stage 1). Runs on the synthetic proxy today; on the
owner's real recorder sessions once hours are banked; on the 3090 for the real
run. Checkpoints go to Koroki Storage on G: (owner's sandbox rule), never C:.

  # prove the machine learns (CPU, ~1 min):
  .venv\\Scripts\\python.exe -m experiments.limbs.idm.train --synthetic --steps 400
  # real run once sessions exist:
  .venv\\Scripts\\python.exe -m experiments.limbs.idm.train --sessions data/demo_recordings --steps 20000
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import torch

from .config import KEY_VOCAB
from .model import IDM, param_count

CKPT_ROOT = Path(r"G:\My Drive\Koroki Storage\datasets\limbs_idm")


def _metrics(out: dict, target: dict) -> dict:
    """Human-readable learning signals, not just loss."""
    with torch.no_grad():
        key_pred = (torch.sigmoid(out["keys"]) >= 0.5).float()
        key_acc = (key_pred == target["keys"]).float().mean().item()
        # F1 on held keys (accuracy alone is inflated by mostly-off labels)
        tp = ((key_pred == 1) & (target["keys"] == 1)).sum().item()
        fp = ((key_pred == 1) & (target["keys"] == 0)).sum().item()
        fn = ((key_pred == 0) & (target["keys"] == 1)).sum().item()
        f1 = tp / (tp + 0.5 * (fp + fn) + 1e-9)
        btn_pred = (torch.sigmoid(out["buttons"]) >= 0.5).float()
        btn_acc = (btn_pred == target["buttons"]).float().mean().item()
        # normalized camera MAE (scale-independent; prior = predict 0 -> ~0.5)
        cam_mae = (out["camera"] - target["camera"]).abs().mean().item()
    return {"key_acc": key_acc, "key_f1": f1, "btn_acc": btn_acc, "cam_mae": cam_mae}


def train_synthetic(steps: int, batch: int, lr: float, device: str) -> dict:
    from . import synthetic

    model = IDM().to(device)
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    print(f"[idm] synthetic run: {param_count(model) / 1e6:.2f}M params, device={device}")
    first = None
    for step in range(1, steps + 1):
        x, target = synthetic.sample_batch(batch)
        x = x.to(device)
        target = {k: v.to(device) for k, v in target.items()}
        out = model(x)
        losses = model.loss(out, target)
        opt.zero_grad()
        losses["total"].backward()
        opt.step()
        if step == 1 or step % max(1, steps // 8) == 0:
            m = _metrics(out, target)
            if first is None:
                first = m
            print(f"  step {step:>5}: loss={losses['total'].item():.3f} "
                  f"key_f1={m['key_f1']:.2f} btn_acc={m['btn_acc']:.2f} "
                  f"cam_mae={m['cam_mae']:.3f}")
    # held-out eval
    x, target = synthetic.sample_batch(256, seed=999)
    x = x.to(device)
    target = {k: v.to(device) for k, v in target.items()}
    final = _metrics(model(x), target)
    print(f"[idm] eval (held-out): key_f1={final['key_f1']:.3f} "
          f"btn_acc={final['btn_acc']:.3f} cam_mae={final['cam_mae']:.3f} "
          f"(prior cam_mae ~0.50)")
    return {"first": first, "final": final, "model": model}


def train_sessions(session_root: Path, steps: int, batch: int, lr: float, device: str) -> dict:
    # Fast path: if the dir holds precached .npz shards, read array slices (no
    # video seeking). Else fall back to decoding sessions on the fly.
    if list(Path(session_root).glob("*.npz")):
        from .precache import CachedCorpus

        corpus = CachedCorpus(session_root)
        print(f"[idm] using precached cache ({len(corpus)} frames)")
    else:
        from .data import SessionCorpus

        corpus = SessionCorpus(session_root)
    if len(corpus) == 0:
        raise SystemExit(f"[idm] no usable frames under {session_root} — record + precache first")
    model = IDM().to(device)
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    print(f"[idm] real run: {len(corpus)} frames, {param_count(model) / 1e6:.2f}M params")
    for step in range(1, steps + 1):
        x, target = corpus.sample_batch(batch)
        x, target = x.to(device), {k: v.to(device) for k, v in target.items()}
        out = model(x)
        losses = model.loss(out, target)
        opt.zero_grad()
        losses["total"].backward()
        opt.step()
        if step % max(1, steps // 20) == 0:
            m = _metrics(out, target)
            print(f"  step {step}: loss={losses['total'].item():.3f} key_f1={m['key_f1']:.2f} "
                  f"btn_acc={m['btn_acc']:.2f} cam_mae={m['cam_mae']:.3f}")
        if step % 5000 == 0:
            save_checkpoint(model, step)
    save_checkpoint(model, steps)
    return {"model": model}


def save_checkpoint(model: IDM, step: int) -> Path:
    try:
        CKPT_ROOT.mkdir(parents=True, exist_ok=True)
        path = CKPT_ROOT / f"idm_step{step}.pt"
        torch.save({"state_dict": model.state_dict(), "key_vocab": KEY_VOCAB, "step": step}, path)
        print(f"[idm] checkpoint -> {path}")
        return path
    except OSError as exc:
        # G: offline -> fall back to a local scratch dir, never lose the run
        local = Path("data/limbs_idm"); local.mkdir(parents=True, exist_ok=True)
        path = local / f"idm_step{step}.pt"
        torch.save({"state_dict": model.state_dict(), "key_vocab": KEY_VOCAB, "step": step}, path)
        print(f"[idm] G: unavailable ({exc}); checkpoint -> {path}")
        return path


def main() -> int:
    ap = argparse.ArgumentParser(description="Train the inverse-dynamics model.")
    ap.add_argument("--synthetic", action="store_true", help="train on the synthetic proxy")
    ap.add_argument("--sessions", default=None, help="recorder-session root for a real run")
    ap.add_argument("--steps", type=int, default=400)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = ap.parse_args()

    t0 = time.perf_counter()
    if args.sessions:
        train_sessions(Path(args.sessions), args.steps, args.batch, args.lr, args.device)
    else:
        train_synthetic(args.steps, args.batch, args.lr, args.device)
    print(f"[idm] done in {time.perf_counter() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
