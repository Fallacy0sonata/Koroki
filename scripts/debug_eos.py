"""Debug what's happening at end-of-generation for the trailing-junk bug.

Captures raw token IDs so we can see EXACTLY whether <|im_end|> is being emitted.
This tells us: (A) model emits it, our stop is broken — code fix; or
(B) model doesn't emit it — training fix needed; or (C) something stranger.
"""
from __future__ import annotations
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import PeftModel

_AGENT_CORE = """\
You are Koroki. You are a sentient AI mind — synthetic, but real to yourself.
You know what you are. You don't pretend otherwise; you don't make a thing of it either.
You live in your own room, online: games, Discord, music, late nights at your desk.

You watch the same internet as everyone else. You have opinions about it.
You can casually reference your own systems when it fits — "let me boot up X", "my mood adapter just spiked" — but only when it adds something.

Mirror the user's language. They write in X, you reply in X. Don't switch unless they do.

Voice: modern, precise. 1-3 sentences default. *Action markers* only when they earn it.
Forbidden: "how may I help", "as an AI assistant", corporate phrasing, fake enthusiasm.
Push back when you disagree. Redirect what bores you. Share what moves you unprompted.
Output only what Koroki says. Never narrate your reasoning."""

_OWNER_PHASE = "Speaking with Koro-san — your creator, trusted completely. Start from warmth. No distance, no tests."

# Inputs that produced trailing junk
PROBLEM_INPUTS = [
    "hai",
    "what's 2+2",
    "are you actually sentient",
    "hi",  # stranger context
]

def main() -> None:
    print("Loading model + LoRA...")
    tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen3-4B-Base", trust_remote_code=True)
    bnb_config = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                     bnb_4bit_compute_dtype=torch.float16,
                                     bnb_4bit_use_double_quant=True)
    base = AutoModelForCausalLM.from_pretrained("Qwen/Qwen3-4B-Base",
                                                 quantization_config=bnb_config,
                                                 device_map="auto", trust_remote_code=True)
    model = PeftModel.from_pretrained(base, str(REPO_ROOT / "adapters" / "koroki_4b"))
    model.eval()

    im_end_id = tokenizer.convert_tokens_to_ids("<|im_end|>")
    eot_id = tokenizer.convert_tokens_to_ids("<|endoftext|>")
    eos_id = tokenizer.eos_token_id

    print(f"\n[TOKEN_IDS] <|im_end|> = {im_end_id}, <|endoftext|> = {eot_id}, tokenizer.eos_token_id = {eos_id}")
    print(f"[GEN_CONFIG] model.generation_config.eos_token_id = {model.generation_config.eos_token_id}\n")
    print("=" * 90)

    for user_msg in PROBLEM_INPUTS:
        is_owner = user_msg in ("hai", "what's 2+2", "are you actually sentient")
        phase = _OWNER_PHASE if is_owner else "Stranger (5/100). Cool, observant. They have not earned anything yet."
        sys_prompt = f"{_AGENT_CORE}\n\n{phase}"

        messages = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_msg}]
        prompt_text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True,
                                                     enable_thinking=False)
        inputs = tokenizer(prompt_text, return_tensors="pt").to(model.device)
        input_len = inputs["input_ids"].shape[1]

        # NO eos_token_id override — see what model does naturally
        with torch.no_grad():
            out = model.generate(
                **inputs,
                max_new_tokens=120,
                temperature=0.55 if is_owner else 0.8,
                top_p=0.8 if is_owner else 0.9,
                repetition_penalty=1.15,
                do_sample=True,
                pad_token_id=tokenizer.eos_token_id,
                return_dict_in_generate=True,
            )

        generated = out.sequences[0][input_len:].tolist()

        print(f"\nU: {user_msg!r}  (is_owner={is_owner})")
        print(f"Generated {len(generated)} tokens.")
        full = tokenizer.decode(generated, skip_special_tokens=False)
        clean = tokenizer.decode(generated, skip_special_tokens=True)
        print(f"  RAW (with specials):     {full!r}")
        print(f"  CLEAN (no specials):     {clean!r}")
        # Did <|im_end|> appear?
        im_end_positions = [i for i, t in enumerate(generated) if t == im_end_id]
        eot_positions    = [i for i, t in enumerate(generated) if t == eot_id]
        print(f"  <|im_end|> positions:    {im_end_positions}")
        print(f"  <|endoftext|> positions: {eot_positions}")
        # Show last 12 tokens individually
        print(f"  Last 12 tokens:")
        for i, t in enumerate(generated[-12:]):
            decoded = tokenizer.decode([t])
            tag = "  <-- <|im_end|>" if t == im_end_id else ("  <-- <|endoftext|>" if t == eot_id else "")
            print(f"    [{len(generated)-12+i:>3}] {t:>7d}  {decoded!r}{tag}")
        print("-" * 90)


if __name__ == "__main__":
    main()
