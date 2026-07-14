"""EXL2 inference bench: load model, measure VRAM / load time / TTFT / tok/s.

Run once per model (separate processes keep VRAM accounting honest):
    python bench_llm.py --repo TheMelonGod/Qwen3-8B-exl2  --rev 8hb-4.5bpw --tag 8b
    python bench_llm.py --repo TheMelonGod/Qwen3-14B-exl2 --rev 8hb-4.5bpw --tag 14b
    python bench_llm.py --repo TheMelonGod/Qwen3-30B-A3B-exl2 --rev 8hb-4.0bpw --tag 30b-a3b

Home reference (4070 Ti, Windows): 8B 4.5bpw = 6.2GB loaded, 56 tok/s.
Linux note: no_sdpa was a WINDOWS-only trap (garbage output); default attention is fine here.
"""
from __future__ import annotations

import argparse
import time

from util import Timer, vram_mib, write_result

PROMPT = ("<|im_start|>system\nYou are a helpful assistant.<|im_end|>\n"
          "<|im_start|>user\nExplain in detail why the sky is blue.<|im_end|>\n"
          "<|im_start|>assistant\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="TheMelonGod/Qwen3-8B-exl2")
    ap.add_argument("--rev", default="8hb-4.5bpw")
    ap.add_argument("--tag", default="8b")
    ap.add_argument("--max-new", type=int, default=256)
    ap.add_argument("--ctx", type=int, default=8192)
    args = ap.parse_args()

    res: dict = {"repo": args.repo, "rev": args.rev, "error": None}
    base_vram = vram_mib()
    try:
        from huggingface_hub import snapshot_download
        with Timer() as t_dl:
            model_dir = snapshot_download(args.repo, revision=args.rev)
        res["download_s"] = round(t_dl.s, 1)

        from exllamav2 import ExLlamaV2, ExLlamaV2Cache, ExLlamaV2Config, ExLlamaV2Tokenizer
        from exllamav2.generator import ExLlamaV2DynamicGenerator

        config = ExLlamaV2Config(model_dir)
        config.max_seq_len = args.ctx
        model = ExLlamaV2(config)
        cache = ExLlamaV2Cache(model, lazy=True)
        with Timer() as t_load:
            model.load_autosplit(cache)
        tokenizer = ExLlamaV2Tokenizer(config)
        # paged mode needs flash-attn; home runs paged=False too (engine_exl2.py)
        gen = ExLlamaV2DynamicGenerator(model=model, cache=cache, tokenizer=tokenizer,
                                        paged=False)
        res["load_s"] = round(t_load.s, 1)
        res["vram_loaded_mib"] = vram_mib() - max(base_vram, 0)

        # three timed generations (first = warmup, reported separately)
        runs = []
        for i in range(3):
            t0 = time.perf_counter()
            out = gen.generate(prompt=PROMPT, max_new_tokens=args.max_new,
                               add_bos=False, encode_special_tokens=True)
            dt = time.perf_counter() - t0
            n_tok = len(tokenizer.encode(out[len(PROMPT):])[0])
            runs.append({"s": round(dt, 2), "tok": n_tok,
                         "tok_s": round(n_tok / dt, 1)})
        res["warmup"] = runs[0]
        res["runs"] = runs[1:]
        res["tok_s_avg"] = round(sum(r["tok_s"] for r in runs[1:]) / 2, 1)
        res["vram_peak_mib"] = vram_mib() - max(base_vram, 0)
    except Exception as exc:  # fail soft — the stage reports instead of dying
        res["error"] = f"{type(exc).__name__}: {exc}"
    write_result(f"llm_{args.tag}", res)


if __name__ == "__main__":
    main()
