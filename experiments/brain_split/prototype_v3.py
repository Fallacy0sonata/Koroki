"""Routed brain prototype v3 — two-gate router (feelings HARD veto) + card context.

v2 finding (docs/brain_split_design.md): routing IDENTITY/FEELING questions to the
raw 8B POISONS her — the reasoner's "I'm just a program, no feelings" framing
contaminated her voice. Owner's fix: the 4B first judges "is this about feelings/
identity?" (semantic, NOT keywords) as a HARD VETO — if yes, gut only, the 8B
never touches it. Only non-personal questions that also need reasoning go to the
consultant. Also: inject the game CARD into the reasoner so game answers land
(v2's aura answer was useless without Sol's RNG knowledge).

Router = two gates:
  gate 1  is_personal? (feelings, her nature, relationships, opinions, support)
          -> if TRUE: gut only, NEVER consult (the veto)
  gate 2  (only if not personal) needs_reasoning? -> consult the 8B
  consult  iff  (NOT is_personal) AND needs_reasoning

Run: .venv_brain2\\Scripts\\python.exe -m experiments.brain_split.prototype_v3
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

# Gate 1 — the veto. Semantic, not keyword.
PERSONAL_SYS = (
    "Decide if this message is PERSONAL: about feelings (yours or theirs), your identity or nature "
    "as an AI, consciousness, your relationship with them, emotional support, comfort, or your own "
    "opinions/preferences. These are things where YOUR authentic take matters and no external "
    "reasoning should ever answer for you.\n"
    "NOT personal: game strategy, math, factual how-to, planning, comparing options — objective "
    "questions with a workable-out answer.\n"
    "Examples: 'are you conscious?'->true | 'do you get lonely?'->true | 'what's the best fruit to "
    "grind?'->false | 'i miss you'->true | 'should i buy the upgrade or a second stall?'->false | "
    "'do you actually like me?'->true. Output JSON only."
)
PERSONAL_SCHEMA = {
    "type": "object",
    "properties": {"is_personal": {"type": "boolean"}, "why": {"type": "string", "maxLength": 80}},
    "required": ["is_personal", "why"],
}
# Gate 2 — reasoning need, only reached for non-personal turns.
REASON_SYS = (
    "This message is NOT personal. Decide if it needs careful multi-step reasoning (game strategy "
    "or optimization, comparing options, math, planning) versus a quick factual/casual reply. "
    "Casual phrasing ('quick', 'just wondering') does not make a strategy question simple. "
    "Output JSON only."
)
REASON_SCHEMA = {
    "type": "object",
    "properties": {"needs_reasoning": {"type": "boolean"}, "why": {"type": "string", "maxLength": 80}},
    "required": ["needs_reasoning", "why"],
}
TRANSITION_SYS = (
    KOROKI_SYS + " You want to think about this one properly. Say ONE short, natural stall line in "
    "your own voice that buys a beat — VARY it, don't always start the same way. Examples of the "
    "vibe (don't copy): 'okay, actually—', 'give me a sec, let me run the numbers', 'oh, this one's "
    "worth thinking about', 'hm. let me look at this properly'. Just the line, no answer yet."
)
REASONER_SYS = (
    "You are a reasoning consultant for a game-playing character. Think step by step using ONLY the "
    "game facts provided (if any), then end with a short concrete CONCLUSION she can act on."
)


def _card(game: str) -> str:
    try:
        import game_knowledge
        return game_knowledge.prompt_summary(game) or ""
    except Exception:
        return ""


# (input, game-card key for reasoner context or None)
TEST_INPUTS = [
    ("haii koroki", None),
    ("do you think you're actually conscious or just pretending really well?", None),  # VETO
    ("do you get lonely when i'm not around?", None),  # VETO
    ("i have $4000, a stall making $12/sec, an upgrade that doubles it for $3500, or a second "
     "stall for $1800. what should i buy?", None),
    ("quick, what's a good aura to hunt for in sol's rng if i only have 2 hours?", "Sol's RNG"),
    ("just wondering, whats the fastest way to level up in blox fruits early game?", "Blox Fruits"),
    ("should i learn python or javascript first if i want to make games?", None),  # deep, not personal
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


def _route(engine, system, schema, inp, key):
    raw = engine.generate_json(chatml(system, inp), json_schema=schema, max_new_tokens=80)
    try:
        return _json.loads(raw)
    except Exception:
        return {}


def main() -> int:
    n = len(TEST_INPUTS)
    personal = [{} for _ in range(n)]
    reason = [{} for _ in range(n)]
    consult_flag = [False] * n
    transition = ["" for _ in range(n)]
    reasoning = ["" for _ in range(n)]
    final = ["" for _ in range(n)]

    print("[v3] 4B+LoRA: two-gate router + gut answers + transitions...", flush=True)
    persona = ExLlamaV2Engine(M4B, lora_dir=LORA, max_seq_len=3072)
    persona.load()
    for i, (inp, key) in enumerate(TEST_INPUTS):
        personal[i] = _route(persona, PERSONAL_SYS, PERSONAL_SCHEMA, inp, key)
        if personal[i].get("is_personal"):
            consult_flag[i] = False
        else:
            reason[i] = _route(persona, REASON_SYS, REASON_SCHEMA, inp, key)
            consult_flag[i] = bool(reason[i].get("needs_reasoning"))
        if consult_flag[i]:
            transition[i] = _gen(persona, TRANSITION_SYS, inp, max_new=40, temp=0.9)
        else:
            final[i] = _gen(persona, KOROKI_SYS, inp)
        print(f"  [{i}] personal={personal[i].get('is_personal')} consult={consult_flag[i]} "
              f":: {inp[:44]}", flush=True)
    _unload(persona)

    consult = [i for i in range(n) if consult_flag[i]]
    if consult:
        print(f"[v3] 8B reasoner (with card context) on {len(consult)}...", flush=True)
        reasoner = ExLlamaV2Engine(M8B, lora_dir=None, max_seq_len=4096)
        reasoner.load()
        for i in consult:
            key = TEST_INPUTS[i][1]
            card = _card(key) if key else ""
            user = (f"game facts:\n{card}\n\nquestion: {TEST_INPUTS[i][0]}" if card
                    else TEST_INPUTS[i][0])
            reasoning[i] = _gen(reasoner, REASONER_SYS, user, max_new=380, temp=0.5)
        _unload(reasoner)

        print("[v3] 4B+LoRA final voice...", flush=True)
        p2 = ExLlamaV2Engine(M4B, lora_dir=LORA, max_seq_len=3072)
        p2.load()
        for i in consult:
            user = (f"{TEST_INPUTS[i][0]}\n\n[your own private reasoning — use it, never mention or "
                    f"quote it, just answer as yourself in one or two lines]:\n{reasoning[i]}")
            final[i] = _gen(p2, KOROKI_SYS, user, max_new=200)
        _unload(p2)

    out = ROOT / "experiments" / "brain_split" / "results_v3.md"
    lines = ["# Brain-split v3 — two-gate router (feelings veto) + card context", ""]
    for i, (inp, key) in enumerate(TEST_INPUTS):
        lines.append(f"## [{i}] {inp}")
        lines.append(f"- **gate1 personal**: {personal[i].get('is_personal')} — {personal[i].get('why','')}")
        if reason[i]:
            lines.append(f"- **gate2 reasoning**: {reason[i].get('needs_reasoning')} — {reason[i].get('why','')}")
        lines.append(f"- **decision**: {'CONSULT 8B' if consult_flag[i] else 'GUT (persona only)'}")
        if transition[i]:
            lines.append(f"- **she says (instant)**: {transition[i]}")
            lines.append(f"- **8B reasoning (hidden, card-fed)**: {reasoning[i][:350]}…")
            lines.append(f"- **final**: {final[i]}")
        else:
            lines.append(f"- **answer**: {final[i]}")
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[v3] -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
