# Koroki Quantization and VRAM Roadmap

## Goal

Keep Koroki in character while making `Brain + TTS` fit more comfortably on a `12 GB` GPU for both web and, if possible, Discord.

The current bottleneck is mostly **resident model weights**, not long-context cache.

## What We Know

- `Qwen3-8B` keeps Koroki's style best, but competes too hard with TTS on a `4070 Ti 12 GB`.
- `Qwen2.5-3B-Instruct` is fast enough for web, but loses too much of Koroki's voice.
- Web got faster once we moved to a lighter profile and a persistent TTS path.
- Discord is currently in a good enough state and should not be destabilized casually.

## Best Research-Backed Levers

### 1. Smarter Brain quantization

Most promising:

- `AWQ`
- `OWQ`
- `Unsloth Dynamic 2.0`

Why:

- These target **weight memory**, which is the real GPU problem.
- They are more likely than a plain smaller model to keep Koroki's tone and reasoning style.

### 2. TTS serving/runtime optimization

Most promising:

- keep TTS resident only in the modes that need it
- keep spoken input short
- explore `vLLM-Omni` or an equivalent optimized Qwen3-TTS serving path later

Why:

- TTS is already smaller than Brain, so the biggest win is usually runtime behavior and serving strategy.

### 3. Memory/context architecture later

Interesting but lower priority:

- Engram-like conditional memory
- KV-cache quantization
- long-context attention improvements

Why:

- These matter more when context growth is the dominant memory problem.
- Koroki's current hard wall is mostly `Brain weights + TTS weights`.

## Execution Order

### Phase A. Build a repeatable benchmark baseline

Use:

- [scripts/benchmark_web_stack.py](C:\Users\Shinn\Desktop\Koroki\scripts\benchmark_web_stack.py)
- [scripts/measure_vram_budget.py](C:\Users\Shinn\Desktop\Koroki\scripts\measure_vram_budget.py)
- [scripts/measure_concurrent_vram.py](C:\Users\Shinn\Desktop\Koroki\scripts\measure_concurrent_vram.py)

Acceptance criteria:

- repeatable TTFT
- repeatable total text latency
- repeatable deferred/persistent voice latency
- peak VRAM snapshots
- qualitative style notes

### Phase B. Safest Brain quantization prototype

Try first:

- a `Qwen3-8B` quantized runtime path that preserves output quality better than the current HF 4-bit path

Candidates:

- `AWQ`
- `Unsloth Dynamic 2.0`

Success means:

- Koroki remains recognizably regal and in character
- Brain TTFT remains acceptable
- TTS can stay available without collapsing the stack

### Phase C. Web-only experiment first

Do not risk Discord first.

Target:

- web uses the quantized Brain candidate
- Discord stays on the current known-good path

Why:

- web is easier to iterate on
- fewer features are required
- one logged-in user model is simpler than Discord's multi-user handling

### Phase D. TTS optimization pass

If Brain quantization is good enough, improve TTS next:

- keep current `0.6B` model
- profile persistent service vs one-shot worker
- later test `vLLM-Omni` or equivalent optimized Qwen3-TTS serving if practical

### Phase E. Promote to Discord only if proven

Only after:

- style is preserved
- VRAM is stable
- no regressions in owner tone or relationship tiers

## Candidate Matrix

### Candidate 0. Current strong baseline

- Brain: `Qwen3-8B`
- Runtime: current HF/PEFT path
- TTS: current persistent/deferred split

Use as style reference only.

### Candidate 1. Web compromise baseline

- Brain: `Qwen2.5-3B-Instruct`
- Runtime: current HF/PEFT path
- TTS: persistent for web

Use as latency reference only.

### Candidate 2. Best next experiment

- Brain: `Qwen3-8B`
- Runtime: better quantized path (`AWQ` or `Dynamic 2.0`)
- TTS: persistent for web

This is the highest-value test.

### Candidate 3. More aggressive fallback

- Brain: stronger quantized `7B/8B` alternative if Qwen3-8B quant path is impractical
- TTS: persistent for web

Use only if the direct Qwen3-8B quant path turns out too painful to integrate.

## Evaluation Rubric

For every candidate, score:

- `style_fidelity`
- `owner_warmth`
- `regal_tone`
- `assistantness_leak`
- `brain_ttft_ms`
- `text_total_ms`
- `voice_ready_ms`
- `peak_vram_mib`
- `subjective_overall`

## Practical Recommendation

Build and compare in this exact order:

1. current strong `8B` baseline
2. current `3B` web baseline
3. first quantized `8B` experiment

If candidate 3 is clearly better than candidate 2 on style and close enough on latency, promote it to web first.
