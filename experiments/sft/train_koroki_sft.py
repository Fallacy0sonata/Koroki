"""
Koroki SFT LoRA trainer — Qwen3-8B + Unsloth QLoRA
Dataset: experiments/sft/koroki_sft_v1.jsonl (ShareGPT format)
Output:  adapters/koroki_personality_v1/

Run from Koroki root with .venv active:
    .venv\Scripts\python.exe experiments\sft\train_koroki_sft.py
"""

import os, sys, pathlib

# Disable Unsloth's fused CE loss — Windows Dynamo bug: chunked loss mismatches
# seq_len vs batch_size when examples exceed MAX_SEQ_LEN after tokenization.
os.environ["UNSLOTH_FUSED_CE_LOSS"] = "0"

ROOT = pathlib.Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from unsloth import FastLanguageModel, is_bfloat16_supported
from unsloth.chat_templates import get_chat_template, standardize_sharegpt
from datasets import load_dataset
from trl import SFTTrainer
from transformers import TrainingArguments, DataCollatorForSeq2Seq

# ── Config ──────────────────────────────────────────────────────────────────
MODEL_NAME    = "Qwen/Qwen3-8B"
MAX_SEQ_LEN   = 1024
LORA_RANK     = 16
LORA_ALPHA    = 32
DATASET_PATH  = "experiments/sft/koroki_sft_v1.jsonl"
OUTPUT_DIR    = "adapters/koroki_personality_v1"
EPOCHS        = 3
LR            = 2e-4
BATCH_SIZE    = 1
GRAD_ACCUM    = 4
WARMUP_RATIO  = 0.05
SAVE_STEPS    = 50
LOG_STEPS     = 10

# ── Load base model in 4-bit ─────────────────────────────────────────────────
print("Loading Qwen3-8B in 4-bit...")
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name=MODEL_NAME,
    max_seq_length=MAX_SEQ_LEN,
    dtype=None,        # auto — bf16 on Ampere+
    load_in_4bit=True,
)

# ── Attach LoRA ──────────────────────────────────────────────────────────────
model = FastLanguageModel.get_peft_model(
    model,
    r=LORA_RANK,
    lora_alpha=LORA_ALPHA,
    target_modules=[
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj",
    ],
    lora_dropout=0,
    bias="none",
    use_gradient_checkpointing="unsloth",
    random_state=42,
    use_rslora=False,
)

# ── Tokenizer — ChatML template ───────────────────────────────────────────────
tokenizer = get_chat_template(tokenizer, chat_template="chatml")

# ── Dataset ──────────────────────────────────────────────────────────────────
print("Loading dataset...")
dataset = load_dataset(
    "json",
    data_files={"train": DATASET_PATH},
    split="train",
)

# Convert ShareGPT {from, value} → {role, content} that apply_chat_template expects
dataset = standardize_sharegpt(dataset)

def apply_template(examples):
    texts = tokenizer.apply_chat_template(
        examples["conversations"],
        tokenize=False,
        add_generation_prompt=False,
    )
    return {"text": texts}

dataset = dataset.map(apply_template, batched=True, remove_columns=dataset.column_names)

print(f"Dataset size: {len(dataset)} examples")
print(f"Sample:\n{dataset[0]['text'][:400]}\n...")

# ── Trainer ───────────────────────────────────────────────────────────────────
use_bf16 = is_bfloat16_supported()
print(f"Training with {'bf16' if use_bf16 else 'fp16'}")

trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=dataset,
    dataset_text_field="text",
    max_seq_length=MAX_SEQ_LEN,
    dataset_num_proc=2,
    packing=False,
    args=TrainingArguments(
        per_device_train_batch_size=BATCH_SIZE,
        gradient_accumulation_steps=GRAD_ACCUM,
        num_train_epochs=EPOCHS,
        learning_rate=LR,
        warmup_ratio=WARMUP_RATIO,
        lr_scheduler_type="cosine",
        fp16=not use_bf16,
        bf16=use_bf16,
        logging_steps=LOG_STEPS,
        save_steps=SAVE_STEPS,
        save_total_limit=2,
        output_dir=OUTPUT_DIR,
        seed=42,
        report_to="none",
        optim="adamw_8bit",
    ),
)

# ── Train ─────────────────────────────────────────────────────────────────────
print(f"\nStarting training: {EPOCHS} epochs, lr={LR}, rank={LORA_RANK}")
print(f"Effective batch size: {BATCH_SIZE * GRAD_ACCUM}")
print(f"Approx optimizer steps: {len(dataset) * EPOCHS // (BATCH_SIZE * GRAD_ACCUM)}\n")

trainer_stats = trainer.train()

print(f"\nTraining done. Loss: {trainer_stats.training_loss:.4f}")

# ── Save adapter ──────────────────────────────────────────────────────────────
print(f"Saving adapter to {OUTPUT_DIR}/...")
model.save_pretrained(OUTPUT_DIR)
tokenizer.save_pretrained(OUTPUT_DIR)
print("Done.")
