"""
Watch generation token-by-token. Specifically: at the position where the
model SHOULD emit <|im_end|> after a complete response, what's actually
in the top-5 probability distribution?
"""
from __future__ import annotations
import sys
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import torch
import torch.nn.functional as F
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

def main() -> None:
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
    print(f"[IDs] <|im_end|>={im_end_id}  <|endoftext|>={eot_id}")
    print(f"[GenConfig] eos_token_id={model.generation_config.eos_token_id}")
    print(f"[Tokenizer] eos_token_id={tokenizer.eos_token_id}")
    # Check if im_end is in the model's bad_words_ids or anywhere weird
    print(f"[GenConfig FULL] {model.generation_config}")
    print()

    # Build prompt for "hey" / owner
    messages = [{"role":"system","content":f"{_AGENT_CORE}\n\n{_OWNER_PHASE}"},
                {"role":"user","content":"hey"}]
    prompt = tokenizer.apply_chat_template(messages, tokenize=False,
                                            add_generation_prompt=True, enable_thinking=False)
    print(f"=== PROMPT TAIL (last 200 chars) ===\n{prompt[-200:]!r}\n")

    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
    input_len = inputs["input_ids"].shape[1]

    # Manual generation loop — print top-5 candidates at every step
    generated = inputs["input_ids"][0].tolist()
    past_key_values = None

    print("Step | Picked token | Top-5 candidates (id, prob, decoded)")
    print("-" * 100)

    for step in range(40):
        # Forward pass
        with torch.no_grad():
            if past_key_values is None:
                out = model(input_ids=inputs["input_ids"], use_cache=True)
            else:
                last_id = torch.tensor([[generated[-1]]], device=model.device)
                out = model(input_ids=last_id, past_key_values=past_key_values, use_cache=True)
            past_key_values = out.past_key_values
            logits = out.logits[0, -1, :]

        # Get top-5 (after temperature=0.55 since that's owner sampling)
        scaled = logits / 0.55
        probs = F.softmax(scaled, dim=-1)
        top5_probs, top5_ids = torch.topk(probs, 5)

        # Sample with temperature 0.55 + top_p 0.8 (matching owner gen settings)
        # For diagnostic, just use greedy here so we see what model "wants"
        picked = top5_ids[0].item()  # greedy

        # Show
        top5_str = " | ".join(
            f"{tid.item():>6}({prob.item()*100:.1f}%) {tokenizer.decode([tid.item()])!r}"
            for tid, prob in zip(top5_ids, top5_probs)
        )
        picked_str = tokenizer.decode([picked])
        marker = ""
        if picked == im_end_id:
            marker = "  <-- <|im_end|> EMITTED!"
        elif picked == eot_id:
            marker = "  <-- <|endoftext|> EMITTED!"
        print(f"[{step:>3}] {picked:>6}={picked_str!r}{marker}")
        if step < 25:  # detailed top-5 for first 25 steps
            print(f"      top-5: {top5_str}")

        generated.append(picked)
        # Stop if we emitted an end token
        if picked in (im_end_id, eot_id):
            print(f"\nStopped on end token at step {step}.")
            break

    print(f"\n=== FULL DECODED OUTPUT ===")
    new_tokens = generated[input_len:]
    print(repr(tokenizer.decode(new_tokens, skip_special_tokens=False)))


if __name__ == "__main__":
    main()
