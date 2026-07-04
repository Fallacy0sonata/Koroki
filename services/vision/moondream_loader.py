"""Loads the vendored moondream2 checkpoint from tools/models/.

Why not transformers AutoModel: the HF dynamic-module loader breaks on
moondream's multi-level relative imports when loading from a local directory
(it fails to copy lora.py into the modules cache). The model code ships
alongside the weights, so we import it directly as a package instead — this is
also moondream's own standalone client path, minus the hub download.

Two checkpoint quirks handled here (found the hard way, 2026-07-03):
- The repo's tokenizer.json is a stale legacy artifact. The 2025 models use
  moondream/starmie-v1 — saved locally as tokenizer_starmie.json. Loading the
  wrong one produces fluent-looking total gibberish.
- weights.py in the repo expects the old tensor naming; the actual safetensors
  uses the HF-wrapper naming (model.* prefix), so we state-dict load directly.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys

logger = logging.getLogger("koroki.vision.loader")

_PKG_NAME = "koroki_moondream2"
TOKENIZER_FILE = "tokenizer_starmie.json"


def _import_model_package(model_dir: str):
    """Import the checkpoint's .py files as a package, whatever the dir is named."""
    if _PKG_NAME in sys.modules:
        return sys.modules[_PKG_NAME]
    init_py = os.path.join(model_dir, "__init__.py")
    if not os.path.exists(init_py):
        open(init_py, "w").close()
    spec = importlib.util.spec_from_file_location(
        _PKG_NAME, init_py, submodule_search_locations=[model_dir]
    )
    pkg = importlib.util.module_from_spec(spec)
    sys.modules[_PKG_NAME] = pkg
    spec.loader.exec_module(pkg)
    return pkg


def load_moondream(model_dir: str, quant: str = "int4"):
    """Build moondream2 on CUDA with the text model weight-only quantized.

    Blocking (~11 s cold: 10 s CPU weight load + 1 s quantize/move). Returns the
    ready-to-use MoondreamModel. VRAM: ~2.5 GiB at int4 (bf16 vision tower +
    int4 text blocks + KV caches).
    """
    import tokenizers
    import torch
    from safetensors.torch import load_file

    _import_model_package(model_dir)
    md_mod = importlib.import_module(f"{_PKG_NAME}.moondream")
    config_mod = importlib.import_module(f"{_PKG_NAME}.config")

    tokenizer_path = os.path.join(model_dir, TOKENIZER_FILE)
    if not os.path.exists(tokenizer_path):
        raise FileNotFoundError(
            f"{tokenizer_path} not found. The checkpoint's own tokenizer.json is the "
            "wrong (legacy) tokenizer — download moondream/starmie-v1 tokenizer.json "
            f"and save it as {TOKENIZER_FILE}."
        )

    class _LocalTokenizer:
        @staticmethod
        def from_pretrained(_name: str):
            return tokenizers.Tokenizer.from_file(tokenizer_path)

    md_mod.Tokenizer = _LocalTokenizer

    model = md_mod.MoondreamModel(config_mod.MoondreamConfig(), setup_caches=False)
    sd = load_file(os.path.join(model_dir, "model.safetensors"))
    sd = {k[len("model."):]: v for k, v in sd.items() if k.startswith("model.")}
    missing, unexpected = model.load_state_dict(sd, strict=False)
    if missing or unexpected:
        logger.warning("state_dict mismatch: missing=%s unexpected=%s", missing, unexpected)
    model.eval()

    # Rope table stays fp32 (reference behavior — HF path never casts buffers).
    freqs_cis = model.text.freqs_cis.clone()
    model.to(torch.bfloat16)
    model.text.freqs_cis = freqs_cis

    if quant in ("int4", "int8"):
        from torchao import quantize_

        if quant == "int4":
            from torchao.quantization import Int4WeightOnlyConfig
            from torchao.quantization.quantize_.workflows.int4.int4_packing_format import (
                Int4PackingFormat,
            )

            cfg = Int4WeightOnlyConfig(
                group_size=128,
                # tile_packed_to_4d = tinygemm in core torch; the 0.17 default
                # ("plain") needs a kernel lib that doesn't exist on Windows.
                int4_packing_format=Int4PackingFormat.TILE_PACKED_TO_4D,
                set_inductor_config=False,
            )
        else:
            from torchao.quantization import Int8WeightOnlyConfig

            cfg = Int8WeightOnlyConfig()
        # Block-by-block: transient VRAM peak stays ~1 block (~100 MB) above final.
        for block in model.text.blocks:
            block.to("cuda")
            quantize_(block, cfg)
    elif quant != "none":
        raise ValueError(f"unknown quant mode: {quant!r} (expected int4|int8|none)")

    model.to("cuda")  # vision/region/wte/lm_head stay bf16; quantized blocks no-op
    model._setup_caches()
    torch.cuda.synchronize()
    return model
