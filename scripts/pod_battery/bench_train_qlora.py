"""8B NF4 QLoRA throughput — sizes the 8B captain retrain (Big Retrain recipe).

Recipe mirrors home: r32/alpha64, batch 2 x grad_accum 16, LR 3e-4, NF4, full-text loss.
Public data only (alpaca-cleaned subset). Reports s/optimizer-step + peak VRAM;
extrapolate at home: total_steps = epochs * ceil(n_samples / 32).
(4B v3 at home: ~2100 samples, 5 epochs, 81 min on the 4070 Ti.)
"""
from __future__ import annotations

import time

import torch

from util import vram_mib, write_result

MODEL_ID = "Qwen/Qwen3-8B"
N_SAMPLES = 256
MAX_STEPS = 12  # ~4 measured steps after warmup is plenty


def main() -> None:
    res: dict = {"model": MODEL_ID, "error": None}
    base_vram = vram_mib()
    try:
        from datasets import load_dataset
        from peft import LoraConfig
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        from trl import SFTConfig, SFTTrainer

        tok = AutoTokenizer.from_pretrained(MODEL_ID)
        bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                 bnb_4bit_compute_dtype=torch.bfloat16,
                                 bnb_4bit_use_double_quant=True)
        t0 = time.perf_counter()
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_ID, quantization_config=bnb, torch_dtype=torch.bfloat16,
            device_map={"": 0})
        res["load_s"] = round(time.perf_counter() - t0, 1)

        ds = load_dataset("yahma/alpaca-cleaned", split=f"train[:{N_SAMPLES}]")

        def to_text(ex):
            prompt = ex["instruction"] + (("\n" + ex["input"]) if ex["input"] else "")
            return {"text": f"<|im_start|>user\n{prompt}<|im_end|>\n"
                            f"<|im_start|>assistant\n{ex['output']}<|im_end|>"}

        ds = ds.map(to_text, remove_columns=ds.column_names)

        lora = LoraConfig(r=32, lora_alpha=64, lora_dropout=0.05,
                          target_modules="all-linear", task_type="CAUSAL_LM")
        cfg = SFTConfig(output_dir="/workspace/qlora_bench", max_steps=MAX_STEPS,
                        per_device_train_batch_size=2, gradient_accumulation_steps=16,
                        learning_rate=3e-4, logging_steps=1, save_strategy="no",
                        bf16=True, max_length=1024, report_to=[])
        trainer = SFTTrainer(model=model, args=cfg, train_dataset=ds,
                             processing_class=tok, peft_config=lora)

        step_times: list[float] = []
        t_last = [time.perf_counter()]

        from transformers import TrainerCallback

        class StepClock(TrainerCallback):
            def on_step_end(self, *a, **k):
                now = time.perf_counter()
                step_times.append(now - t_last[0])
                t_last[0] = now

        trainer.add_callback(StepClock())
        torch.cuda.reset_peak_memory_stats()
        trainer.train()

        measured = step_times[2:]  # drop warmup steps
        res["s_per_step"] = round(sum(measured) / len(measured), 2)
        res["samples_per_step"] = 32
        res["peak_vram_torch_mib"] = int(torch.cuda.max_memory_allocated() / 2**20)
        res["peak_vram_smi_mib"] = vram_mib() - max(base_vram, 0)
        # convenience: hours for a home-sized run (2100 samples x 5 epochs)
        steps_home = 5 * -(-2100 // 32)
        res["est_hours_2100x5ep"] = round(steps_home * res["s_per_step"] / 3600, 2)
    except Exception as exc:
        res["error"] = f"{type(exc).__name__}: {exc}"
    write_result("train_qlora_8b", res)


if __name__ == "__main__":
    main()
