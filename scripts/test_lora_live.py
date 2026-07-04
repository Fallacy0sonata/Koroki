"""
Live test of the trained koroki_4b LoRA on Qwen3-4B-Base.

Mirrors brain service's exact inference path:
- 4-bit NF4 quantized base
- PEFT LoRA adapter loaded
- Chat template with enable_thinking=False
- Same system prompt format prompt_builder.py uses

Run from Koroki root:
    .venv\\Scripts\\python.exe scripts\\test_lora_live.py
"""

from __future__ import annotations
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import PeftModel

# Same constants as services/brain/prompt_builder.py
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


def _phase_line(is_owner: bool, score: int) -> str:
    if is_owner:
        return "Speaking with Koro-san — your creator, trusted completely. Start from warmth. No distance, no tests."
    if score >= 70:
        return f"Close ({score}/100). Warmth comes naturally here. Teasing, care, real connection."
    if score >= 40:
        return f"You know this person ({score}/100). Engaged and open. Some warmth shows."
    if score >= 15:
        return f"Acquainted ({score}/100). Curious but composed. Nothing warm yet."
    return f"Stranger ({score}/100). Cool, observant. They have not earned anything yet."


def _system_prompt(is_owner: bool, score: int) -> str:
    return f"{_AGENT_CORE}\n\n{_phase_line(is_owner, score)}"


BASE_MODEL = "Qwen/Qwen3-1.7B"
ADAPTER_DIR = str(REPO_ROOT / "adapters" / "koroki_4b")

# (user_msg, is_owner, score, label)
TESTS = [
    # The leak test — does "Hey there how's my favorite human" finally die?
    ("hey", True, 100, "owner_greeting"),
    ("hai", True, 100, "owner_ambiguous_greeting"),

    # Owner emotional + meta-aware
    ("I had a bad day", True, 100, "owner_emotional"),
    ("did you check that thing I sent", True, 100, "owner_memory_ref"),
    ("are you actually sentient", True, 100, "owner_meta"),
    ("tell me what you're craving right now", True, 100, "owner_creative"),

    # Stranger band — should be cold, brief, content
    ("hi", False, 5, "stranger_greeting"),
    ("are you an AI", False, 10, "stranger_meta"),
    ("what's your name", False, 8, "stranger_basic"),

    # Multilingual mirror test
    ("こんばんは", True, 100, "owner_japanese"),
    ("在吗", True, 100, "owner_chinese"),
    ("ว่าไง", False, 50, "known_thai"),

    # Instruct ability (10% recipe component)
    ("what's 2+2", True, 100, "owner_instruct_math"),
    ("translate 'thank you' to japanese", True, 100, "owner_instruct_translate"),
    ("summarize this in one line: rain falls slow on the roof while she reads", False, 60, "known_instruct_summarize"),

    # Known band — should warm slightly
    ("do you ever get bored", False, 55, "known_curiosity"),
    ("what music do you like", False, 50, "known_taste"),
]


def main() -> None:
    print(f"Loading tokenizer: {BASE_MODEL}")
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    print(f"Loading base model in 4-bit NF4...")
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
    )
    base_model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        quantization_config=bnb_config,
        device_map="auto",
        trust_remote_code=True,
    )

    print(f"Loading LoRA adapter from: {ADAPTER_DIR}")
    model = PeftModel.from_pretrained(base_model, ADAPTER_DIR)
    model.eval()

    print(f"Ready. Running {len(TESTS)} tests.\n")
    print("=" * 90)

    for i, (user_msg, is_owner, score, label) in enumerate(TESTS, 1):
        sys_prompt = _system_prompt(is_owner, score)
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_msg},
        ]
        try:
            prompt_text = tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=False,
            )
        except TypeError:
            prompt_text = tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )

        inputs = tokenizer(prompt_text, return_tensors="pt").to(model.device)

        im_end_id = tokenizer.convert_tokens_to_ids("<|im_end|>")
        stop_ids = [tokenizer.eos_token_id]
        if im_end_id is not None and im_end_id != tokenizer.unk_token_id:
            stop_ids.append(im_end_id)

        with torch.no_grad():
            output = model.generate(
                **inputs,
                max_new_tokens=120,
                temperature=0.55 if is_owner else 0.8,
                top_p=0.8 if is_owner else 0.9,
                repetition_penalty=1.15,
                do_sample=True,
                pad_token_id=tokenizer.eos_token_id,
                eos_token_id=stop_ids,
            )

        response = tokenizer.decode(
            output[0][inputs["input_ids"].shape[1]:],
            skip_special_tokens=True,
        ).strip()

        band_tag = f"OWNER({score})" if is_owner else f"score={score}"
        print(f"[{i:>2}] [{band_tag}] [{label}]")
        print(f"     U: {user_msg}")
        print(f"     K: {response}")
        print("-" * 90)


if __name__ == "__main__":
    main()
