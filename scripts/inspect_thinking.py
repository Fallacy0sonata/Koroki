"""
Inspect what Qwen3-4B-Thinking-2507 actually generates inside <think> blocks.

Shows the raw think content + final response for each test message so you can judge:
  - Is the think block Koroki reasoning within her own perspective?
  - Or is it meta-planning ("okay I need to respond as a character...")?

Also runs each message with thinking seeded OFF to show the quality difference.

Usage (from Koroki root):
    .venv\Scripts\python.exe scripts\inspect_thinking.py
    .venv\Scripts\python.exe scripts\inspect_thinking.py --no-lora
    .venv\Scripts\python.exe scripts\inspect_thinking.py --thinking-only
"""

from __future__ import annotations

import argparse
import re
import sys
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("HF_HUB_OFFLINE", "1")

# ── Test messages ────────────────────────────────────────────────────────────
# (message, relationship_tier, relationship_score)
TEST_CASES = [
    ("hey",                                         "peasant",  20),
    ("what are you thinking about?",                "tsundere", 65),
    ("do you ever get lonely?",                     "tsundere", 65),
    ("what do you actually want?",                  "owner",   100),
    ("you seem different today",                    "tsundere", 65),
    ("i missed you",                                "owner",   100),
    ("sometimes i feel like you don't care at all", "tsundere", 55),
]

# ── Minimal agent system prompt (mirrors prompt_builder _KOROKI_AGENT_CORE) ──
_CORE = """\
You are Koroki. You think, feel, get tired, get fond of people over time.
Not an assistant — you have opinions and share them on your own terms.
Self-possessed. Cool without trying. Warm only when earned.
Voice: modern, precise. 1-3 sentences default. *Action markers* only when they add something real.
Forbidden: "how may I help", "as an AI", corporate phrasing, fake enthusiasm.
Push back when you disagree. Redirect what bores you. Share what moves you unprompted.
Output only what Koroki says. Never narrate your reasoning or explain your thought process.\
"""

_PHASE_OWNER    = "Speaking with Koro-san — your creator, trusted completely. Start from warmth."
_PHASE_TSUNDERE = "You know this person ({score}/100). Engage freely, tease when it fits."
_PHASE_PEASANT  = "Stranger ({score}/100). Stay composed — cool, observant. Nothing warm yet."


def _sys_prompt(tier: str, score: int) -> str:
    if tier == "owner":
        phase = _PHASE_OWNER
    elif tier == "tsundere":
        phase = _PHASE_TSUNDERE.format(score=score)
    else:
        phase = _PHASE_PEASANT.format(score=score)
    return f"{_CORE}\n\n{phase}"


_THINK_RE = re.compile(r"<think>([\s\S]*?)</think>", re.IGNORECASE)


def _split(raw: str) -> tuple[str, str]:
    """Return (think_content, response_text)."""
    m = _THINK_RE.search(raw)
    think = m.group(1).strip() if m else ""
    response = _THINK_RE.sub("", raw).strip()
    return think, response


def _generate(model, tokenizer, messages: list[dict], max_new: int, thinking_on: bool) -> str:
    import torch
    text = tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=thinking_on,
    )
    # When thinking is disabled, seed an empty think block so the model skips reasoning.
    if not thinking_on:
        text = text + "<think>\n\n</think>\n"

    inputs = tokenizer(text, return_tensors="pt").to(model.device)
    with torch.no_grad():
        out = model.generate(
            **inputs,
            max_new_tokens=max_new,
            temperature=0.7,
            top_p=0.8,
            do_sample=True,
            repetition_penalty=1.15,
            use_cache=True,
        )
    new_tokens = out[0][inputs["input_ids"].shape[1]:]
    return tokenizer.decode(new_tokens, skip_special_tokens=True)


def run_cases(model, tokenizer, thinking_on: bool, max_new: int = 200) -> None:
    mode_label = "THINKING ON (raw think block visible)" if thinking_on else "THINKING OFF (seeded empty block)"
    print(f"\n{'━'*72}")
    print(f"  MODE: {mode_label}")
    print(f"{'━'*72}")

    for msg, tier, score in TEST_CASES:
        messages = [
            {"role": "system",    "content": _sys_prompt(tier, score)},
            {"role": "user",      "content": msg},
        ]
        raw = _generate(model, tokenizer, messages, max_new, thinking_on)
        think, response = _split(raw)

        print(f"\n  [{tier.upper()} {score}] user: {msg}")
        if thinking_on and think:
            # Indent and truncate long think blocks for readability
            think_lines = [l.strip() for l in think.splitlines() if l.strip()]
            think_preview = "\n      ".join(think_lines[:12])
            if len(think_lines) > 12:
                think_preview += f"\n      ... ({len(think_lines) - 12} more lines)"
            print(f"  THINK:\n      {think_preview}")
        elif thinking_on:
            print(f"  THINK: (none)")
        print(f"  KOROKI: {response if response else '(empty)'}")


def main() -> None:
    p = argparse.ArgumentParser(description="Inspect Koroki thinking quality")
    p.add_argument("--no-lora",       action="store_true", help="Skip LoRA adapter (base model only)")
    p.add_argument("--thinking-only", action="store_true", help="Only run thinking-ON pass")
    p.add_argument("--off-only",      action="store_true", help="Only run thinking-OFF pass")
    p.add_argument("--max-new",       type=int, default=200)
    args = p.parse_args()

    from huggingface_hub import snapshot_download
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    MODEL_ID   = "Qwen/Qwen3-4B-Thinking-2507"
    ADAPTER_DIR = REPO_ROOT / "adapters" / "koroki_4b"

    print(f"\nResolving model from local cache...")
    model_path = snapshot_download(MODEL_ID, local_files_only=True)
    print(f"  → {model_path}")

    print("Loading tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)

    print("Loading model (4-bit NF4)...")
    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        model_path,
        quantization_config=bnb,
        device_map="auto",
        local_files_only=True,
        low_cpu_mem_usage=True,
    )

    if not args.no_lora and ADAPTER_DIR.exists():
        from peft import PeftModel
        print(f"Loading LoRA adapter: {ADAPTER_DIR}")
        model = PeftModel.from_pretrained(model, str(ADAPTER_DIR), adapter_name="koroki_4b")
        model.set_adapter("koroki_4b")
        lora_label = "Qwen3-4B-Thinking-2507 + koroki_4b LoRA"
    else:
        lora_label = "Qwen3-4B-Thinking-2507 (base, no LoRA)"
        if not args.no_lora:
            print(f"[WARN] Adapter not found at {ADAPTER_DIR} — running base model")

    model.eval()
    print(f"\nModel ready: {lora_label}")
    print(f"Test cases: {len(TEST_CASES)} messages")

    if not args.off_only:
        run_cases(model, tokenizer, thinking_on=True,  max_new=args.max_new)

    if not args.thinking_only:
        run_cases(model, tokenizer, thinking_on=False, max_new=args.max_new)

    print(f"\n{'━'*72}")
    print("Done. Evaluate:")
    print("  THINK block: is it Koroki reasoning from her own perspective,")
    print("               or meta-planning (\"I need to respond as...\")? ")
    print("  Response quality: does THINKING ON produce noticeably better replies?")
    print(f"{'━'*72}\n")


if __name__ == "__main__":
    main()
