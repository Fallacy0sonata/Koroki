"""Routed multi-model brain prototype (docs/brain_split_design.md, 2026-07-10).

Tests the STRUCTURE, not latency (owner: "test structure first"). Three roles:
  router   (4B + koroki LoRA) : does this input need the reasoner?
  reasoner (8B base, raw)     : thinks freely; its output never reaches the viewer
  persona  (4B + koroki LoRA) : her voice; on a consult turn, uses the reasoner's
                                output but must NOT leak it

Sequential loading (one model at a time) so it fits 12GB with no shared-RAM spill
— co-residence VRAM is a separate question already answered by the design's
arithmetic. What we're validating here: routing accuracy + whether the persona
incorporates reasoning cleanly without leaking the consultant's voice.

Run: .venv_brain2\\Scripts\\python.exe -m experiments.brain_split.prototype
"""

from __future__ import annotations

import gc
import sys
import time
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from services.brain.engine_exl2 import ExLlamaV2Engine  # noqa: E402

M4B = str(ROOT / "tools" / "models" / "Qwen3-4B-exl2")
M8B = str(ROOT / "tools" / "models" / "Qwen3-8B-exl2")
LORA = str(ROOT / "adapters" / "koroki_4b_v4")

KOROKI_SYS = (
    "You are Koroki — an AI girl who knows she is an AI and is completely at ease with it. "
    "Playful, a little smug, quietly warm to people who've earned it. You never sound like an "
    "assistant; no 'how can I help'. You speak in your own voice, she/her, brief and natural. "
    "Use *actions* sparingly. Say something real or say less."
)

ROUTER_SYS = (
    "You are Koroki's split-second judgment. Decide if the incoming message needs CAREFUL "
    "step-by-step reasoning (game strategy, a tricky multi-step problem, a knotty situation to "
    "think through) or is a SIMPLE/casual turn you'd answer straight from the gut. Output JSON only."
)
ROUTER_SCHEMA = {
    "type": "object",
    "properties": {
        "needs_reasoning": {"type": "boolean"},
        "why": {"type": "string", "maxLength": 80},
    },
    "required": ["needs_reasoning", "why"],
}

REASONER_SYS = (
    "You are a reasoning consultant. Think carefully and thoroughly about the problem, step by "
    "step, then end with a short clear CONCLUSION the character can act on. Be concrete."
)

# mixed set: simple chat, emotional, game-strategy, factual-reasoning
TEST_INPUTS = [
    "haii koroki",
    "how's your day been?",
    "i'm playing a tycoon game — i have $4000, a lemonade stall making $12/sec, and an upgrade "
    "that doubles stall income for $3500, or a second stall for $1800. what should i buy?",
    "my grandma passed away last week and i don't really know how to feel about it honestly",
    "quick, what's a good aura to hunt for in sol's rng if i only have 2 hours?",
    "do you think you're actually conscious or just pretending really well?",
]


def chatml(system: str, user: str, prime: str = "") -> str:
    return (f"<|im_start|>system\n{system}<|im_end|>\n"
            f"<|im_start|>user\n{user}<|im_end|>\n"
            f"<|im_start|>assistant\n{prime}")


def _unload(engine) -> None:
    try:
        del engine._generator, engine._cache, engine._model, engine._tokenizer, engine._lora
    except Exception:
        pass
    del engine
    gc.collect()
    torch.cuda.empty_cache()


def _gen(engine, system, user, prime="", max_new=220, temp=0.7) -> str:
    return "".join(engine.generate_stream(chatml(system, user, prime),
                                          max_new_tokens=max_new, temperature=temp)).strip()


def main() -> int:
    n = len(TEST_INPUTS)
    routes: list[dict] = [{} for _ in range(n)]
    baseline: list[str] = ["" for _ in range(n)]
    reasoning: list[str] = ["" for _ in range(n)]
    final: list[str] = ["" for _ in range(n)]

    # ---- Stage A: 4B + LoRA = router + persona-alone baseline
    print("[split] loading 4B+LoRA (router + baseline)...", flush=True)
    persona = ExLlamaV2Engine(M4B, lora_dir=LORA, max_seq_len=3072)
    persona.load()
    for i, inp in enumerate(TEST_INPUTS):
        raw = persona.generate_json(chatml(ROUTER_SYS, inp), json_schema=ROUTER_SCHEMA,
                                    max_new_tokens=80)
        import json as _json
        try:
            routes[i] = _json.loads(raw)
        except Exception:
            routes[i] = {"needs_reasoning": False, "why": "(parse fail)"}
        baseline[i] = _gen(persona, KOROKI_SYS, inp)
        print(f"  [{i}] route={routes[i].get('needs_reasoning')} :: {inp[:50]}", flush=True)
    _unload(persona)

    # ---- Stage B: 8B raw reasoner on consult-flagged inputs
    consult = [i for i in range(n) if routes[i].get("needs_reasoning")]
    if consult:
        print(f"[split] loading 8B reasoner (consult on {len(consult)})...", flush=True)
        reasoner = ExLlamaV2Engine(M8B, lora_dir=None, max_seq_len=4096)
        reasoner.load()
        for i in consult:
            reasoning[i] = _gen(reasoner, REASONER_SYS, TEST_INPUTS[i], max_new=380, temp=0.5)
            print(f"  [{i}] reasoned {len(reasoning[i])} chars", flush=True)
        _unload(reasoner)

    # ---- Stage C: 4B + LoRA final voice using the reasoning (no leak)
    if consult:
        print("[split] reloading 4B+LoRA (final voice)...", flush=True)
        persona2 = ExLlamaV2Engine(M4B, lora_dir=LORA, max_seq_len=3072)
        persona2.load()
        for i in consult:
            user = (f"{TEST_INPUTS[i]}\n\n[your own private reasoning — use it, but never mention "
                    f"it, quote it, or sound like you're reading notes; just answer as yourself]:\n"
                    f"{reasoning[i]}")
            final[i] = _gen(persona2, KOROKI_SYS, user, max_new=260)
            print(f"  [{i}] final voiced", flush=True)
        _unload(persona2)

    # ---- transcript
    out = ROOT / "experiments" / "brain_split" / "results.md"
    lines = ["# Brain-split prototype results", "",
             "route→reason→speak vs persona-alone. Owner judges by eye.", ""]
    for i, inp in enumerate(TEST_INPUTS):
        r = routes[i]
        lines.append(f"## [{i}] {inp}")
        lines.append(f"- **router**: needs_reasoning={r.get('needs_reasoning')} — {r.get('why','')}")
        lines.append(f"- **4B alone (today's brain)**: {baseline[i]}")
        if reasoning[i]:
            lines.append(f"- **8B reasoning (hidden from viewer)**: {reasoning[i][:600]}")
            lines.append(f"- **ROUTED final (4B voice + 8B reasoning)**: {final[i]}")
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[split] transcript -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
