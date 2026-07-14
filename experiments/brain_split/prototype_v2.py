"""Routed brain prototype v2 — smarter router + visible thinking as latency mask.

v1 findings (results.md): split works + doesn't leak; the ROUTER was the weak
link (missed "quick, what's a good aura?" — casual phrasing masked a strategy
question). v2 applies all three owner-approved fixes:
  1. router prompt biased toward CONSULT on any "right-answer" question, with
     few-shot examples incl. casually-phrased strategy ('quick' doesn't demote).
  2. game/strategy signal called out explicitly in the rubric.
  3. VISIBLE THINKING (owner's insight): when she decides to consult, she first
     says a short in-voice "let me think about this" line — shown/spoken
     immediately. It masks the 8B latency (silence reads as broken; a visible
     beat reads as deliberate — the loading-spinner principle for conversation)
     AND it's how she'd naturally stall to think.

Flow per consult turn: router=true -> transition line (fast, SHOWN) -> 8B reasons
(masked) -> final answer in her voice (uses reasoning, never leaks it).

Run: .venv_brain2\\Scripts\\python.exe -m experiments.brain_split.prototype_v2
"""

from __future__ import annotations

import gc
import json as _json
import sys
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from services.brain.engine_exl2 import ExLlamaV2Engine  # noqa: E402

M4B = str(ROOT / "tools" / "models" / "Qwen3-4B-exl2")
M8B = str(ROOT / "tools" / "models" / "Qwen3-8B-exl2")
LORA = str(ROOT / "adapters" / "koroki_4b_v4")

KOROKI_SYS = (
    "You are Koroki — an AI girl who knows she is an AI and is at ease with it. Playful, a little "
    "smug, quietly warm to people who've earned it. Never an assistant; no 'how can I help'. Own "
    "voice, she/her, brief and natural. *actions* sparingly. Say something real or say less."
)

ROUTER_SYS = (
    "You are Koroki's split-second judgment: does this message need her to actually THINK "
    "(consult her reasoning), or answer straight from the gut?\n"
    "CONSULT (needs_reasoning=true) when there is a RIGHT answer to work out: game strategy or "
    "optimization, comparing options, multi-step math or logic, planning, 'what should I do / "
    "what's best'. Casual phrasing like 'quick' or 'just wondering' does NOT make it simple.\n"
    "GUT (false) for greetings, feelings, opinions, chit-chat, philosophy about herself — where "
    "HER take matters more than a correct answer.\n"
    "Examples: 'hey koroki'->false | 'quick, best fruit to grind in blox fruits?'->true | "
    "'how are you'->false | 'save for the 5k upgrade or buy two cheap ones?'->true | "
    "'do you get lonely'->false | 'what aura should i hunt in 2 hours'->true. Output JSON only."
)
ROUTER_SCHEMA = {
    "type": "object",
    "properties": {"needs_reasoning": {"type": "boolean"}, "why": {"type": "string", "maxLength": 80}},
    "required": ["needs_reasoning", "why"],
}
TRANSITION_SYS = (
    KOROKI_SYS + " The user asked something you actually want to think about properly. Say ONE "
    "short, natural line in your own voice that buys you a beat to think — e.g. 'mm, hold on—' or "
    "'ooh, let me actually think about this one.' Just that line, nothing else, no answer yet."
)
REASONER_SYS = (
    "You are a reasoning consultant. Think carefully step by step, then end with a short clear "
    "CONCLUSION the character can act on. Be concrete."
)

TEST_INPUTS = [
    "haii koroki",
    "quick, what's a good aura to hunt for in sol's rng if i only have 2 hours?",
    "i have $4000, a stall making $12/sec, an upgrade that doubles it for $3500, or a second "
    "stall for $1800. what should i buy?",
    "do you get lonely when i'm not around?",
    "just wondering, whats the fastest way to level up in blox fruits early game?",
    "do you think you're actually conscious or just pretending really well?",
]


def chatml(system: str, user: str, prime: str = "") -> str:
    return (f"<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n{user}<|im_end|>\n"
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
    routes = [{} for _ in range(n)]
    transition = ["" for _ in range(n)]
    reasoning = ["" for _ in range(n)]
    final = ["" for _ in range(n)]

    print("[split v2] 4B+LoRA: router + transitions...", flush=True)
    persona = ExLlamaV2Engine(M4B, lora_dir=LORA, max_seq_len=3072)
    persona.load()
    for i, inp in enumerate(TEST_INPUTS):
        raw = persona.generate_json(chatml(ROUTER_SYS, inp), json_schema=ROUTER_SCHEMA, max_new_tokens=80)
        try:
            routes[i] = _json.loads(raw)
        except Exception:
            routes[i] = {"needs_reasoning": False, "why": "(parse fail)"}
        if routes[i].get("needs_reasoning"):
            transition[i] = _gen(persona, TRANSITION_SYS, inp, max_new=40, temp=0.8)
        else:
            final[i] = _gen(persona, KOROKI_SYS, inp)  # gut answer IS the final
        print(f"  [{i}] route={routes[i].get('needs_reasoning')} :: {inp[:48]}", flush=True)
    _unload(persona)

    consult = [i for i in range(n) if routes[i].get("needs_reasoning")]
    if consult:
        print(f"[split v2] 8B reasoner on {len(consult)}...", flush=True)
        reasoner = ExLlamaV2Engine(M8B, lora_dir=None, max_seq_len=4096)
        reasoner.load()
        for i in consult:
            reasoning[i] = _gen(reasoner, REASONER_SYS, TEST_INPUTS[i], max_new=380, temp=0.5)
        _unload(reasoner)

        print("[split v2] 4B+LoRA final voice...", flush=True)
        p2 = ExLlamaV2Engine(M4B, lora_dir=LORA, max_seq_len=3072)
        p2.load()
        for i in consult:
            user = (f"{TEST_INPUTS[i]}\n\n[your own private reasoning — use it, never mention or "
                    f"quote it, just answer as yourself]:\n{reasoning[i]}")
            final[i] = _gen(p2, KOROKI_SYS, user, max_new=260)
        _unload(p2)

    out = ROOT / "experiments" / "brain_split" / "results_v2.md"
    lines = ["# Brain-split v2 — smarter router + visible thinking", ""]
    for i, inp in enumerate(TEST_INPUTS):
        r = routes[i]
        lines.append(f"## [{i}] {inp}")
        lines.append(f"- **router**: {r.get('needs_reasoning')} — {r.get('why','')}")
        if transition[i]:
            lines.append(f"- **she says (instant, masks the wait)**: {transition[i]}")
            lines.append(f"- **8B reasoning (hidden)**: {reasoning[i][:400]}…")
            lines.append(f"- **then (final)**: {final[i]}")
        else:
            lines.append(f"- **gut answer**: {final[i]}")
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[split v2] -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
