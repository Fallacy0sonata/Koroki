"""Build the CosyVoice2 flow-estimator TensorRT engine OFFLINE (OPT-O2).

Produces pretrained_models/CosyVoice2-0.5B/flow.decoder.estimator.fp16.mygpu.plan
from the fp32 ONNX the model download ships. Run this before enabling
models.tts.cosyvoice_load_trt so the adapter never blocks minutes building
in-process at startup. Rebuild whenever the GPU or driver generation changes
(TRT plans are device-specific — "mygpu" is literal).

Run from repo root in the CosyVoice venv:
  .venv_cosyvoice\\Scripts\\python.exe scripts\\build_cosyvoice_trt.py

Needs ~2-4 GB free VRAM for the TRT builder — unload the voice model first if
the stack is up (POST :9004/unload, then :9004/load after).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CV_REPO = REPO_ROOT / "experiments" / "cosyvoice" / "CosyVoice"
MODEL_DIR = REPO_ROOT / "experiments" / "cosyvoice" / "pretrained_models" / "CosyVoice2-0.5B"

sys.path.insert(0, str(CV_REPO))
sys.path.insert(0, str(CV_REPO / "third_party" / "Matcha-TTS"))


def main() -> int:
    onnx_path = MODEL_DIR / "flow.decoder.estimator.fp32.onnx"
    plan_path = MODEL_DIR / "flow.decoder.estimator.fp16.mygpu.plan"
    if not onnx_path.exists():
        print(f"ONNX missing: {onnx_path}")
        return 1
    if plan_path.exists() and plan_path.stat().st_size > 0:
        print(f"Plan already exists: {plan_path} ({plan_path.stat().st_size/1e6:.0f} MB)")
        print("Delete it to force a rebuild.")
        return 0

    from cosyvoice.utils.file_utils import convert_onnx_to_trt

    # Shapes mirror cosyvoice.cli.model.CosyVoiceModel.get_trt_kwargs — keep in
    # sync if the repo revision changes them.
    trt_kwargs = {
        "min_shape": [(2, 80, 4), (2, 1, 4), (2, 80, 4), (2, 80, 4)],
        "opt_shape": [(2, 80, 500), (2, 1, 500), (2, 80, 500), (2, 80, 500)],
        "max_shape": [(2, 80, 3000), (2, 1, 3000), (2, 80, 3000), (2, 80, 3000)],
        "input_names": ["x", "mask", "mu", "cond"],
    }

    print(f"Building TRT engine (fp16) from {onnx_path.name} ...")
    t0 = time.time()
    convert_onnx_to_trt(str(plan_path), trt_kwargs, str(onnx_path), fp16=True)
    print(f"Done in {time.time() - t0:.0f}s -> {plan_path} "
          f"({plan_path.stat().st_size/1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
