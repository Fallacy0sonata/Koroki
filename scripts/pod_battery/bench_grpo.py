"""GRPO pilot VRAM probe — settles the research-arc conflict.

DeepSeek estimated 12-14GB, Gemini 22-23.5GB for GRPO G=4 on an 8B NF4 base.
The rollout generation is the VRAM hog, so completion length matters most:
we measure at 512 and 2048 completion tokens (8k-ctx pilot scales from there).
Peak VRAM is the deliverable; speed is secondary (derate 4090->3090 ~40%).
"""
from __future__ import annotations

import time

import torch

from util import vram_mib, write_result

MODEL_ID = "Qwen/Qwen3-8B"


def run_probe(max_completion: int) -> dict:
    out: dict = {"max_completion": max_completion, "error": None}
    base_vram = vram_mib()
    try:
        from datasets import Dataset
        from peft import LoraConfig
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        from trl import GRPOConfig, GRPOTrainer

        tok = AutoTokenizer.from_pretrained(MODEL_ID)
        bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                 bnb_4bit_compute_dtype=torch.bfloat16,
                                 bnb_4bit_use_double_quant=True)
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_ID, quantization_config=bnb, torch_dtype=torch.bfloat16,
            device_map={"": 0})

        prompts = [f"Compute step by step: what is {a} * {b}?"
                   for a, b in [(17, 23), (41, 12), (33, 27), (55, 19)] * 4]
        ds = Dataset.from_dict({"prompt": prompts})

        def reward_has_digits(completions, **kwargs):
            return [1.0 if any(c.isdigit() for c in comp) else 0.0
                    for comp in completions]

        lora = LoraConfig(r=32, lora_alpha=64, target_modules="all-linear",
                          task_type="CAUSAL_LM")
        # TRL 1.7 dropped max_prompt_length (prompts here are ~20 tokens anyway)
        cfg = GRPOConfig(output_dir="/workspace/grpo_bench", max_steps=4,
                         num_generations=4, per_device_train_batch_size=4,
                         gradient_accumulation_steps=1, learning_rate=1e-5,
                         max_completion_length=max_completion,
                         logging_steps=1, save_strategy="no", bf16=True, report_to=[])
        trainer = GRPOTrainer(model=model, args=cfg, train_dataset=ds,
                              reward_funcs=reward_has_digits, peft_config=lora)
        torch.cuda.reset_peak_memory_stats()
        t0 = time.perf_counter()
        trainer.train()
        out["train_4steps_s"] = round(time.perf_counter() - t0, 1)
        out["peak_vram_torch_mib"] = int(torch.cuda.max_memory_allocated() / 2**20)
        out["peak_vram_smi_mib"] = vram_mib() - max(base_vram, 0)
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--completion", type=int, default=512,
                    help="max completion tokens (run once per value; fresh "
                         "process keeps VRAM accounting honest)")
    args = ap.parse_args()
    res = run_probe(args.completion)
    write_result(f"grpo_8b_c{args.completion}", res)


if __name__ == "__main__":
    main()
