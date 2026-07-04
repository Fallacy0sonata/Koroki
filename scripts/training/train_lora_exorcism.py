from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from torch.utils.data import Dataset
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    Trainer,
    TrainingArguments,
)
from peft import LoraConfig, TaskType, get_peft_model, prepare_model_for_kbit_training


class ChatJsonlDataset(Dataset):
    def __init__(self, path: Path, tokenizer, max_length: int = 1024) -> None:
        self.rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            messages = rec.get("messages", [])
            if len(messages) < 2:
                continue
            if hasattr(tokenizer, "apply_chat_template"):
                text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)
            else:
                text = f"User: {messages[0]['content']}\nAssistant: {messages[1]['content']}"
            toks = tokenizer(
                text,
                truncation=True,
                max_length=max_length,
                padding="max_length",
                return_tensors="pt",
            )
            input_ids = toks["input_ids"][0]
            attn = toks["attention_mask"][0]
            labels = input_ids.clone()
            labels[attn == 0] = -100
            self.rows.append({"input_ids": input_ids, "attention_mask": attn, "labels": labels})

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, idx: int):
        return self.rows[idx]


def main() -> None:
    parser = argparse.ArgumentParser(description="Train Koroki LoRA (Exorcism phase)")
    parser.add_argument("--tier", choices=["owner", "tsundere", "peasant"], required=True)
    parser.add_argument("--base-model", default="Qwen/Qwen2.5-3B-Instruct")
    parser.add_argument("--data-dir", default="data/training/lora")
    parser.add_argument("--output-dir", default="adapters")
    parser.add_argument("--epochs", type=float, default=1.0)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--grad-accum", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--warmup-ratio", type=float, default=0.03)
    parser.add_argument("--lora-r", type=int, default=32)
    parser.add_argument("--lora-alpha", type=int, default=64)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--max-length", type=int, default=1024)
    parser.add_argument("--logging-steps", type=int, default=1)
    parser.add_argument("--save-steps", type=int, default=200)
    parser.add_argument("--gradient-checkpointing", action="store_true")
    parser.add_argument("--load-in-4bit", action="store_true")
    args = parser.parse_args()

    train_path = Path(args.data_dir) / f"{args.tier}_sft.jsonl"
    if not train_path.exists():
        raise FileNotFoundError(f"Dataset not found: {train_path}")

    tokenizer = AutoTokenizer.from_pretrained(args.base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    load_kwargs = {
        "torch_dtype": torch.float16 if torch.cuda.is_available() else torch.float32,
        "device_map": None,
    }
    if args.load_in_4bit:
        load_kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
        )
        load_kwargs["device_map"] = {"": 0} if torch.cuda.is_available() else None

    model = AutoModelForCausalLM.from_pretrained(args.base_model, **load_kwargs)
    model.config.use_cache = False
    if torch.cuda.is_available() and not args.load_in_4bit:
        model = model.to("cuda")
    if args.load_in_4bit:
        model = prepare_model_for_kbit_training(model)

    lora_cfg = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        task_type=TaskType.CAUSAL_LM,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "up_proj", "down_proj", "gate_proj"],
        bias="none",
    )
    model = get_peft_model(model, lora_cfg)
    if args.gradient_checkpointing:
        model.gradient_checkpointing_enable()
        if hasattr(model, "enable_input_require_grads"):
            model.enable_input_require_grads()

    dataset = ChatJsonlDataset(train_path, tokenizer, max_length=args.max_length)
    if len(dataset) == 0:
        raise RuntimeError(f"No usable training rows in {train_path}")

    out_dir = Path(args.output_dir) / args.tier
    out_dir.mkdir(parents=True, exist_ok=True)

    training_args = TrainingArguments(
        output_dir=str(out_dir),
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        num_train_epochs=args.epochs,
        learning_rate=args.learning_rate,
        lr_scheduler_type="cosine",
        warmup_ratio=args.warmup_ratio,
        logging_steps=args.logging_steps,
        save_steps=args.save_steps,
        save_total_limit=2,
        fp16=torch.cuda.is_available(),
        bf16=False,
        report_to=[],
        remove_unused_columns=False,
    )

    trainer = Trainer(model=model, args=training_args, train_dataset=dataset)
    trainer.train()

    losses = [
        float(item["loss"])
        for item in trainer.state.log_history
        if isinstance(item, dict) and "loss" in item
    ]
    first_five = losses[:5]
    min_first_five = min(first_five) if first_five else None

    stats = {
        "tier": args.tier,
        "dataset_size": len(dataset),
        "epochs": args.epochs,
        "learning_rate": args.learning_rate,
        "lr_scheduler": "cosine",
        "lora_r": args.lora_r,
        "lora_alpha": args.lora_alpha,
        "losses": losses,
        "first_five_losses": first_five,
        "min_first_five_loss": min_first_five,
        "overfit_risk_first_five_under_0_1": bool(min_first_five is not None and min_first_five < 0.1),
    }
    (out_dir / "training_stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")

    model.save_pretrained(str(out_dir))
    tokenizer.save_pretrained(str(out_dir))
    print(f"Saved LoRA adapter to {out_dir}")
    print(json.dumps({
        "first_five_losses": first_five,
        "min_first_five_loss": min_first_five,
        "overfit_risk_first_five_under_0_1": stats["overfit_risk_first_five_under_0_1"],
    }, indent=2))


if __name__ == "__main__":
    main()
