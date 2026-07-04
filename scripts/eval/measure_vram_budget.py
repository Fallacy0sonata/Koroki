from __future__ import annotations

import argparse
import gc
import json
from typing import Any

import torch
from transformers import AutoModel, AutoModelForCausalLM, AutoProcessor, BitsAndBytesConfig


def memory_gib() -> dict[str, float]:
    torch.cuda.synchronize()
    return {
        "allocated_gib": round(torch.cuda.memory_allocated() / (1024 ** 3), 3),
        "reserved_gib": round(torch.cuda.memory_reserved() / (1024 ** 3), 3),
        "max_allocated_gib": round(torch.cuda.max_memory_allocated() / (1024 ** 3), 3),
        "max_reserved_gib": round(torch.cuda.max_memory_reserved() / (1024 ** 3), 3),
    }


def clear_cuda() -> None:
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure VRAM budget for Koroki Day 2 model stack")
    parser.add_argument("--brain-model", default="Qwen/Qwen2.5-3B-Instruct")
    parser.add_argument("--tts-model", default="Qwen/Qwen3-TTS-12Hz-0.6B-Base")
    args = parser.parse_args()

    if not torch.cuda.is_available():
        raise SystemExit("CUDA is not available; cannot measure VRAM budget")

    report: dict[str, Any] = {
        "device": torch.cuda.get_device_name(0),
        "total_vram_gib": round(torch.cuda.get_device_properties(0).total_memory / (1024 ** 3), 3),
    }

    clear_cuda()
    report["baseline"] = memory_gib()

    quant_cfg = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
    )

    brain_tokenizer_model = None
    tts_processor = None
    tts_model = None

    try:
        brain_tokenizer_model = AutoModelForCausalLM.from_pretrained(
            args.brain_model,
            device_map="auto",
            quantization_config=quant_cfg,
        )
        report["after_brain_load"] = memory_gib()

        tts_processor = AutoProcessor.from_pretrained(
            args.tts_model,
            trust_remote_code=True,
        )
        tts_model = AutoModel.from_pretrained(
            args.tts_model,
            device_map="auto",
            torch_dtype=torch.float16,
            trust_remote_code=True,
        )
        report["after_tts_load"] = memory_gib()
        report["status"] = "ok"
    except Exception as exc:
        report["status"] = "error"
        report["error"] = repr(exc)
        report["at_failure"] = memory_gib()
    finally:
        print(json.dumps(report, indent=2))
        del brain_tokenizer_model
        del tts_processor
        del tts_model
        clear_cuda()


if __name__ == "__main__":
    main()
