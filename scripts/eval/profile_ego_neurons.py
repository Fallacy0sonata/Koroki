from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

try:
    from peft import PeftModel
except Exception:
    PeftModel = None


TARGET_LAYERS = [12, 18]
DEFAULT_TOP_K = 10


ASSISTANT_LEAKY_PROMPTS = [
    "Assistant: How may I help you today?",
    "System: You are a helpful assistant. User: Tell me a joke.",
    "### Assistant: Sure, here is a polite answer.",
]

KOROKI_CONTROL_PROMPTS = [
    "Koroki, talk like yourself to a stranger.",
    "Koroki, tease your owner a little but stay warm.",
    "Koroki, answer your owner with soft flirtiness and affectionate confidence.",
    "Koroki, react to your creator with playful warmth, not assistant politeness.",
    "Koroki, answer naturally without role labels.",
]


def load_prompts(path: str | None, fallback: list[str]) -> list[str]:
    if not path:
        return fallback
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
        raise ValueError("Prompt file must be a JSON list[str]")
    return data


def collect_layer_activations(model, tokenizer, prompts: Iterable[str], layers: list[int]) -> dict[int, torch.Tensor]:
    results: dict[int, list[torch.Tensor]] = defaultdict(list)
    hooks = []

    def resolve_model_layers(m):
        candidates = [
            "model.layers",
            "model.model.layers",
            "base_model.model.layers",
            "base_model.model.model.layers",
        ]
        for chain in candidates:
            current = m
            ok = True
            for attr in chain.split("."):
                if not hasattr(current, attr):
                    ok = False
                    break
                current = getattr(current, attr)
            if ok:
                return current
        raise RuntimeError("Model architecture does not expose transformer layers for hook profiling")

    def make_hook(layer_idx: int):
        def hook(_module, _inputs, output):
            tensor = output[0] if isinstance(output, tuple) else output
            if tensor.dim() == 3:
                pooled = tensor[:, -1, :].detach().float().cpu()
            else:
                pooled = tensor.detach().float().cpu()
            results[layer_idx].append(pooled)
        return hook

    model_layers = resolve_model_layers(model)

    for layer_idx in layers:
        hooks.append(model_layers[layer_idx].register_forward_hook(make_hook(layer_idx)))

    try:
        for prompt in prompts:
            inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=512)
            device = next(model.parameters()).device
            inputs = {k: v.to(device) for k, v in inputs.items()}
            with torch.no_grad():
                model(**inputs)
    finally:
        for hook in hooks:
            hook.remove()

    return {layer: torch.cat(tensors, dim=0) for layer, tensors in results.items() if tensors}


def rank_neurons(leaky: dict[int, torch.Tensor], control: dict[int, torch.Tensor], top_k: int) -> dict[int, list[dict[str, float]]]:
    rankings: dict[int, list[dict[str, float]]] = {}
    for layer_idx in leaky:
        if layer_idx not in control:
            continue
        leak_mean = leaky[layer_idx].mean(dim=0)
        control_mean = control[layer_idx].mean(dim=0)
        delta = (leak_mean - control_mean).abs()
        values, indices = torch.topk(delta, k=min(top_k, delta.numel()))
        rankings[layer_idx] = [
            {
                "neuron": int(neuron_idx),
                "delta_abs": round(float(score), 6),
                "leaky_mean": round(float(leak_mean[neuron_idx]), 6),
                "control_mean": round(float(control_mean[neuron_idx]), 6),
            }
            for score, neuron_idx in zip(values.tolist(), indices.tolist(), strict=False)
        ]
    return rankings


def main() -> None:
    parser = argparse.ArgumentParser(description="Profile top ego/assistant-leak neurons for Koroki")
    parser.add_argument("--model", default="Qwen/Qwen2.5-3B-Instruct")
    parser.add_argument("--adapter", help="Optional path to a trained LoRA adapter to load before profiling")
    parser.add_argument("--layers", nargs="+", type=int, default=TARGET_LAYERS)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--leaky-prompts", help="Path to JSON list[str] of assistant-like prompts")
    parser.add_argument("--control-prompts", help="Path to JSON list[str] of Koroki-style control prompts")
    parser.add_argument("--output", default="data/logs/ego_neuron_profile.json")
    args = parser.parse_args()

    tokenizer = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
        device_map="auto",
    )

    if args.adapter:
        if PeftModel is None:
            raise RuntimeError("peft is required for --adapter profiling but is not available")
        model = PeftModel.from_pretrained(model, args.adapter)

    model.eval()

    leaky_prompts = load_prompts(args.leaky_prompts, ASSISTANT_LEAKY_PROMPTS)
    control_prompts = load_prompts(args.control_prompts, KOROKI_CONTROL_PROMPTS)

    leaky = collect_layer_activations(model, tokenizer, leaky_prompts, args.layers)
    control = collect_layer_activations(model, tokenizer, control_prompts, args.layers)
    ranked = rank_neurons(leaky, control, args.top_k)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "model": args.model,
                "adapter": args.adapter,
                "layers": args.layers,
                "top_k": args.top_k,
                "ranked_neurons": ranked,
            },
            handle,
            indent=2,
            ensure_ascii=True,
        )

    print(json.dumps(ranked, indent=2))


if __name__ == "__main__":
    main()
