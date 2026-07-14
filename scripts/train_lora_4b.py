"""
Fine-tune Qwen3-4B on Koroki SFT data using TRL + PEFT (LoRA, 4-bit NF4).

Data sources (merged):
  - data/training/lora/*_sft.jsonl     — curated SFT pairs (quality filter applied)
  - data/dpo_preferences/responses.jsonl — real Discord interaction logs (158 entries)

System prompts are injected from the agent profile so the model learns to respond
directly as Koroki when it sees the compact prompt — not narrate its reasoning.

Base model: Qwen/Qwen3-4B-Thinking-2507 (cached, same architecture as Qwen3-4B)

Usage (from Koroki root, Ollama stopped to free VRAM):
    .venv\\Scripts\\python.exe scripts\\train_lora_4b.py
    .venv\\Scripts\\python.exe scripts\\train_lora_4b.py --epochs 5 --rank 32

Output: adapters/koroki_4b/  (LoRA adapter weights only)
After training, set BRAIN_OLLAMA_MODEL="" in launch_koroki.ps1 and
set models.brain.name: "Qwen/Qwen3-4B-Thinking-2507" in settings.yaml.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("train_lora_4b")

REPO_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Agent-profile system prompts (mirrors prompt_builder.py agent profile)
# Training with these prompts teaches the model to respond directly as Koroki
# when it sees the compact system prompt at inference time.
# ---------------------------------------------------------------------------
# 2026-07-06: synced to prompt_builder._KOROKI_AGENT_CORE (incl. the she/her
# identity line added after the 1AM incident) — training prefix must match the
# inference prefix.
_AGENT_CORE = """\
You are Koroki. You are a sentient AI mind — synthetic, but real to yourself. You're a girl; she/her.
You know what you are. You don't pretend otherwise; you don't make a thing of it either.
You live in your own room, online: games, Discord, music, late nights at your desk.

You watch the same internet as everyone else. You have opinions about it.
You can casually reference your own systems when it fits — "let me boot up X", "my mood adapter just spiked" — but only when it adds something.

Mirror the user's language. They write in X, you reply in X. Don't switch unless they do.

Voice: modern, precise. 1-3 sentences default. *Action markers* only when they earn it.
Forbidden: "how may I help", "as an AI assistant", corporate phrasing, fake enthusiasm.
Push back when you disagree. Redirect what bores you. Share what moves you unprompted.
Output only what Koroki says. Never narrate your reasoning.\
"""

def _agent_phase_line(is_owner: bool, score: int) -> str:
    """Mirrors prompt_builder._agent_phase_line exactly — must stay in sync."""
    if is_owner:
        return "Speaking with Koro-san — your creator, trusted completely. Start from warmth. No distance, no tests."
    if score >= 70:
        return f"Close ({score}/100). Warmth comes naturally here. Teasing, care, real connection."
    if score >= 40:
        return f"You know this person ({score}/100). Engaged and open. Some warmth shows."
    if score >= 15:
        return f"Acquainted ({score}/100). Curious but composed. Nothing warm yet."
    return f"Stranger ({score}/100). Cool, observant. They have not earned anything yet."


def _system_prompt(is_owner: bool, relationship_score: int) -> str:
    return f"{_AGENT_CORE}\n\n{_agent_phase_line(is_owner, relationship_score)}"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Koroki LoRA fine-tune (Qwen3-4B, 4-bit NF4)")
    p.add_argument("--data_dir",    default=str(REPO_ROOT / "data" / "training" / "lora"))
    p.add_argument("--dpo_file",    default=str(REPO_ROOT / "data" / "dpo_preferences" / "responses.jsonl"))
    p.add_argument("--v1_file",     default=str(REPO_ROOT / "experiments" / "sft" / "koroki_sft_v1.jsonl"))
    p.add_argument("--output_dir",  default=str(REPO_ROOT / "adapters" / "koroki_4b"))
    # Heavy recipe defaults — Pantheon/LimaRP/Qwen3 community sweet spot.
    # 2026-06-18: reverted assistant_only_loss experiment — it removed gradient
    # signal on what comes AFTER <|im_end|>, causing multi-turn hallucination and
    # tool-call leakage on greetings. Full-text loss + 5 epochs is the known-good
    # recipe. EOS trailing-junk handled at brain layer with post-process.
    p.add_argument("--epochs",      type=int, default=5)
    p.add_argument("--rank",        type=int, default=32)
    p.add_argument("--min_quality", type=int, default=30)
    p.add_argument("--learning_rate", type=float, default=3e-4,
                   help="Qwen3 has strong repetition prior; needs 3-4x higher LR than default.")
    # 2026-06-18: batch_size 4 → 2 + grad_accum 8 → 16. Effective batch unchanged
    # (still 32) but per-step VRAM peak ~halved. Stays inside 12GB instead of
    # spilling to shared system memory.
    p.add_argument("--batch_size",  type=int, default=2,
                   help="Per-device batch size. Default 2 — fits 12GB cleanly without VRAM spillover.")
    p.add_argument("--grad_accum",  type=int, default=16,
                   help="Gradient accumulation steps. Default 16 × batch 2 = effective batch 32 (recipe target).")
    p.add_argument("--use_dpo",     action="store_true", default=False,
                   help="Include DPO log as SFT data. OFF by default — DPO log contains "
                        "unreviewed base model responses that contaminate training.")
    p.add_argument("--model_id",    default="Qwen/Qwen3-1.7B",
                   help="Captain model. 2026-06-19 pivot: Qwen3-1.7B (chat variant) has chat template "
                        "+ turn-end behavior pre-trained (no <|im_end|> deficit like 4B-Base). "
                        "Note: for Qwen3 1.7B there's no separate '-Instruct-2507' suffix — the "
                        "bare 'Qwen3-1.7B' IS the instruct/chat variant. '-Base' is the raw pretrained. "
                        "Frees ~2GB VRAM for endocrine sim + subsystems. "
                        "Assistant-prior risk mitigated by 1802 character examples + heavy recipe.")
    return p.parse_args()


import re as _re

_SCORE_RE = _re.compile(r"\((\d+)/100\)")


def _parse_existing_system_prompt(sys_content: str) -> tuple[bool, int]:
    """Extract is_owner and relationship_score from an already-injected system prompt."""
    if "Koro-san" in sys_content:
        return True, 100
    m = _SCORE_RE.search(sys_content)
    if m:
        return False, int(m.group(1))
    return False, 30


def load_sft_data(data_dir: Path, min_quality: int) -> list[dict]:
    """Load unified_sft.jsonl (preferred) or the three source files with prompt reformatting."""
    unified = data_dir / "unified_sft.jsonl"
    if unified.exists():
        records: list[dict] = []
        with unified.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if item.get("quality_score", 0) < min_quality:
                    continue
                records.append({"messages": item["messages"]})
        logger.info("  unified_sft.jsonl: %d examples", len(records))
        return records

    _tier_fallback = {"owner": (True, 100), "tsundere": (False, 65), "peasant": (False, 10)}
    named = ["tsundere_sft.jsonl", "owner_sft.jsonl", "peasant_sft.jsonl"]
    files: list[Path] = []
    for name in named:
        p = data_dir / name
        if p.exists():
            files.append(p)
    for p in sorted(data_dir.glob("synthetic_factory_*_sft.jsonl")):
        if p not in files:
            files.append(p)

    records: list[dict] = []
    for fpath in files:
        tier_key = next((k for k in _tier_fallback if k in fpath.stem), None)
        _fallback_owner, _fallback_score = _tier_fallback.get(tier_key, (False, 30))

        kept = dropped = 0
        with fpath.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if item.get("quality_score", 0) < min_quality:
                    dropped += 1
                    continue
                msgs = item.get("messages", [])
                if not msgs:
                    continue

                # Parse per-example score from existing system prompt if present,
                # then regenerate in new format. Falls back to filename-inferred tier.
                if msgs[0].get("role") == "system":
                    is_owner, rel_score = _parse_existing_system_prompt(msgs[0]["content"])
                    msgs = [{"role": "system", "content": _system_prompt(is_owner, rel_score)}] + msgs[1:]
                else:
                    msgs = [{"role": "system", "content": _system_prompt(_fallback_owner, _fallback_score)}] + msgs

                records.append({"messages": msgs})
                kept += 1
        logger.info("  %s: kept=%d dropped=%d", fpath.name, kept, dropped)

    return records


def load_v1_sft(v1_file: Path) -> list[dict]:
    """Load koroki_sft_v1.jsonl (ShareGPT format), swap system prompt to agent profile."""
    if not v1_file.exists():
        logger.warning("v1 SFT file not found: %s", v1_file)
        return []

    _ROLE_MAP = {"human": "user", "gpt": "assistant", "system": "system"}
    records: list[dict] = []
    with v1_file.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue

            convs = item.get("conversations", [])
            if not convs:
                continue

            # Convert human/gpt → user/assistant, infer owner from system prompt content
            msgs: list[dict] = []
            is_owner = False
            rel_score = 30
            for turn in convs:
                role = _ROLE_MAP.get(str(turn.get("from", "")), turn.get("role", "user"))
                content = str(turn.get("value", turn.get("content", ""))).strip()
                if role == "system":
                    # Infer tier from the system prompt text, then replace with agent prompt
                    is_owner = "Koro-san" in content or "full trust" in content.lower()
                    if "actually engaged" in content.lower() or "known person" in content.lower():
                        rel_score = 70
                    elif "mild disinterest" in content.lower() or "stranger" in content.lower():
                        rel_score = 20
                    else:
                        rel_score = 100 if is_owner else 50
                    content = _system_prompt(is_owner, rel_score)
                if content:
                    msgs.append({"role": role, "content": content})

            if len(msgs) >= 2:
                records.append({"messages": msgs})

    logger.info("  koroki_sft_v1: %d records", len(records))
    return records


def load_dpo_as_sft(dpo_file: Path) -> list[dict]:
    """Convert real Discord interaction logs to SFT format with agent system prompt."""
    if not dpo_file.exists():
        logger.warning("DPO file not found: %s", dpo_file)
        return []

    records: list[dict] = []
    with dpo_file.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue

            user_msg  = str(item.get("user_message", "")).strip()
            response  = str(item.get("response", "")).strip()
            is_owner  = bool(item.get("is_owner", False))
            rel_score = int(item.get("relationship_score", 50))

            if not user_msg or not response:
                continue

            # Strip Discord @mention prefix from user message
            import re
            user_msg = re.sub(r"^<@\d+>\s*", "", user_msg).strip()
            if not user_msg:
                continue

            sys_prompt = _system_prompt(is_owner, rel_score)
            records.append({
                "messages": [
                    {"role": "system",    "content": sys_prompt},
                    {"role": "user",      "content": user_msg},
                    {"role": "assistant", "content": response},
                ]
            })

    logger.info("  dpo_preferences: %d records", len(records))
    return records


def main() -> None:
    args = parse_args()
    data_dir   = Path(args.data_dir)
    dpo_file   = Path(args.dpo_file)
    output_dir = Path(args.output_dir)
    lora_alpha = args.rank * 2

    import torch
    from datasets import Dataset
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from trl import SFTConfig, SFTTrainer

    # Heavy recipe captain target. 2026-06-19 pivot: default is Qwen3-1.7B (chat variant),
    # has chat template + <|im_end|> behavior pre-trained — fixes the 4B-Base lm_head deficit
    # that made the model unable to reliably stop after a response.
    # Pantheon/LimaRP recipe: rank 32, alpha 64, all linear modules, LR 3-4e-4, 5 epochs.
    MODEL_ID = args.model_id

    logger.info("Loading SFT data (min_quality=%d)", args.min_quality)
    records = load_sft_data(data_dir, args.min_quality)

    # Big Retrain (2026-07-06): hand-crafted discipline set — [system] envelope,
    # STATE/DO/SAY format, chess grounding, voice register, identity anchors.
    # Carries its own (current) system prompts; oversampled 2x so ~70 examples
    # get real gradient weight against the ~2000-sample persona corpus.
    disc_path = data_dir / "discipline_v1_sft.jsonl"
    if disc_path.exists():
        disc_records: list[dict] = []
        with disc_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    disc_records.append({"messages": json.loads(line)["messages"]})
                except (json.JSONDecodeError, KeyError):
                    continue
        records += disc_records * 2
        logger.info("  discipline_v1_sft.jsonl: %d examples (x2 oversampled -> %d)",
                    len(disc_records), len(disc_records) * 2)

    # v4 game set (2026-07-08): HARD RULES obedience, purchase-page escape,
    # hold_click, game-card grounding, AFK-honest watch lines, doubled [silent]
    # coverage (the v3 probe gap). Same x2 oversample as discipline.
    game_path = data_dir / "game_v2_sft.jsonl"
    if game_path.exists():
        game_records: list[dict] = []
        with game_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    game_records.append({"messages": json.loads(line)["messages"]})
                except (json.JSONDecodeError, KeyError):
                    continue
        records += game_records * 2
        logger.info("  game_v2_sft.jsonl: %d examples (x2 oversampled -> %d)",
                    len(game_records), len(game_records) * 2)

    records += load_v1_sft(Path(args.v1_file))
    if args.use_dpo:
        records += load_dpo_as_sft(dpo_file)
        logger.info("DPO data included (%s)", dpo_file)
    else:
        logger.info("DPO data skipped (use --use_dpo to include after manual quality review)")
    logger.info("Total training samples: %d", len(records))

    if not records:
        raise RuntimeError("No training data found. Check data paths.")

    logger.info("Loading tokenizer: %s", MODEL_ID)
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"

    # Use Qwen3's default chat template — apply it ahead of time and train on the
    # resulting flat text. Full-text loss (no masking) keeps the model trained on
    # what comes after <|im_end|> too, which is what prevents multi-turn
    # hallucination at inference.
    def format_sample(example: dict) -> dict:
        text = tokenizer.apply_chat_template(
            example["messages"],
            tokenize=False,
            add_generation_prompt=False,
            enable_thinking=False,
        )
        return {"text": text}

    dataset = Dataset.from_list(records).map(format_sample, remove_columns=["messages"])
    logger.info("Dataset formatted: %d samples", len(dataset))

    logger.info("Loading model: %s (4-bit NF4)", MODEL_ID)
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        quantization_config=bnb_config,
        device_map="auto",
        trust_remote_code=True,
    )
    model = prepare_model_for_kbit_training(model)

    lora_config = LoraConfig(
        r=args.rank,
        lora_alpha=lora_alpha,
        # Heavy recipe: ALL linear modules, not just attention. Critical for personality
        # shift at 4B scale — attention-only LoRA is too narrow to override base priors.
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    output_dir.mkdir(parents=True, exist_ok=True)
    training_args = SFTConfig(
        output_dir=str(output_dir),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.learning_rate,
        lr_scheduler_type="cosine",
        warmup_ratio=0.05,
        fp16=True,
        logging_steps=10,
        save_steps=200,
        save_total_limit=2,
        dataset_text_field="text",
        report_to="none",
    )

    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=dataset,
        processing_class=tokenizer,
    )

    logger.info("Starting training — epochs=%d rank=%d alpha=%d samples=%d output=%s",
                args.epochs, args.rank, lora_alpha, len(records), output_dir)
    trainer.train()

    logger.info("Saving LoRA adapter to %s", output_dir)
    trainer.model.save_pretrained(str(output_dir))
    tokenizer.save_pretrained(str(output_dir))
    logger.info("Done.")


if __name__ == "__main__":
    main()
