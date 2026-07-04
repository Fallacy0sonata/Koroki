# Koroki Humanization Roadmap

The goal: make Koroki less reactive and more like a person who happens to be online.
Humans don't wait to be asked — they have moods, they bring things up unprompted, they
remember what bothered them, they learn what makes people tick. This file tracks the
ideas and research behind making that real.

---

## Research Sources

| # | Link | Core idea for us |
|---|------|-----------------|
| 1 | https://deepmind.google/blog/alphaevolve-impact/ | Evolutionary self-improvement loop — feedback from output quality refines future behavior |
| 2 | https://www.anthropic.com/research/emotion-concepts-function | Emotion vectors are real activation patterns that drive downstream behavior, not just labels |
| 3 | https://api-docs.deepseek.com/news/news260424 | Hybrid thinking/non-thinking modes — adaptive compute allocation per task |
| 4 | https://arxiv.org/abs/2603.15031 | Attention Residuals — weighted selective aggregation of prior layer outputs |
| 5 | https://arxiv.org/abs/2510.25741 | Looped LMs (Ouro) — latent reasoning loops embedded in pretraining, dynamic depth |

---

## Feature Ideas

---

### 1. Inter-Service Feedback Loops (user's butterfly effect fix)

**Problem:** The pipeline is fire-and-forget at every boundary. Small misinterpretations
compound. Brain generates slightly off-tone → TTS picks wrong emotion → output is wrong
and nobody knows which stage broke.

**Idea:** Each service confirms what it understood before the next stage proceeds.

**How:**
```
Orchestrator → Brain:
  sends: { message, emotion_vector, intended_affect }
  receives: { text, expressed_emotion }   ← Brain reports what tone it thinks it used
  Orchestrator validates: if expressed_emotion diverges from emotion_vector by > threshold → log + optionally retry with stronger steering

Brain → Orchestrator → TTS:
  sends: { text, emotion_tags, target_affect }
  receives: { audio_b64, applied_tags }   ← TTS reports which tags it actually applied
  Orchestrator can detect if IndexTTS silently dropped [crying] or [whispering] tags
```

**Inspired by:** Paper #2 (emotion vectors as real control surfaces), user's observation.

**Implementation path:**
- Phase 1: Brain appends a brief JSON block after its response: `{expressed_emotion: "warm"}`.
  Parse it in orchestrator. Don't change model — use a tiny post-generation text classifier
  on the output if structured output is unreliable.
- Phase 2: IndexTTS adapter.py returns `applied_tags` alongside audio.
- Phase 3: Orchestrator logs divergence. Over time, spot patterns (e.g. emotion X always
  gets interpreted as Y by TTS) and add correction rules.

**Priority: HIGH** — architectural, no model changes needed, reduces compounding drift.

---

### 2. Emotion Vector Propagation (Brain knows Koroki's current state)

**Problem:** Orchestrator computes an emotion vector (e.g. "worried: 0.7, affectionate: 0.4")
but only passes it to TTS as tags. Brain doesn't receive the full emotion state — just whatever
is embedded in the system prompt. Brain generates from the prompt but doesn't "feel" the state.

**Idea:** Pass the live emotion vector into Brain's context as a structured block. Brain's
response is influenced by it. Then Brain's expressed_emotion (from Feature 1) can be compared
to what was sent.

**How:** System prompt injection (already done for personality tier). Extend it:
```
[KOROKI_STATE]
emotion: worried(0.7), affectionate(0.4), curious(0.2)
relationship_score: 83
current_topic: game they've been discussing for 3 exchanges
[/KOROKI_STATE]
```
No model change. Prompt-level. But makes the state explicit rather than implied.

**Inspired by:** Paper #2 (functional emotion states alter behavior; can be artificially stimulated).

**Priority: MEDIUM** — low effort, measurable difference in response character coherence.

---

### 3. Reward/Punishment System — DPO Fine-tuning

**The idea:** Yes, this is directly how modern LLMs (GPT-4, Claude) were aligned. It's called
RLHF (Reinforcement Learning from Human Feedback). The lighter version we can run locally is
DPO (Direct Preference Optimization) — same concept, no separate reward model needed.

**Why it matters:** Prompt engineering hits a hard ceiling. The underlying Qwen3-8B weights
are still generic. DPO actually shifts the weights toward preferred behavior — Koroki gets
more in-character at the parameter level, not just surface-prompted.

**How DPO works:**
- Collect triplets: (prompt, good_response, bad_response)
- good = response that sounds like Koroki, in-character, natural
- bad = response that's assistant-like, robotic, wrong emotion, breaks character
- Run DPO training. Model learns to prefer the "good" distribution.

**Data collection (start now, train later):**
- Option A: Discord reactions. 👍 on a Koroki message = preferred. 👎 = rejected.
  Log both with full conversation context. Automatic preference data accumulation.
- Option B: Shadow generation. For each response, also generate an "assistant mode"
  response (strip the guillotine system prompt). Mark the in-character version as chosen,
  assistant version as rejected. Infinite automatic preference pairs.
- Option C: Periodic manual review. Read logs, mark 10-20 good vs bad per session.

**Training:**
- Use `unsloth` — supports Qwen3-8B, 4-bit DPO, much faster than HuggingFace PEFT
- Can run on same GPU as normal inference (just not simultaneously)
- Start training after ~200+ preference pairs

**RLAIF variant (AI feedback):**
- Use a stronger judge model (Claude via API, or a local 70B if we ever have it) to rate
  Koroki's responses on: in-character-ness, emotion coherence, naturalness (1-5 scale)
- Auto-generate preference data at scale without manual review

**Inspired by:** AlphaEvolve (#1) evolutionary loop, standard RLHF literature.

**Priority: MEDIUM-HIGH** — start data collection immediately (just needs a Discord reaction
logger), training can wait until enough pairs accumulate.

---

### 4. Proactive Behavior — Koroki Initiates

**Problem:** Koroki is purely reactive. She only responds. A person would bring things up,
check in, remember something from yesterday, share a thought unprompted.

**Idea:** Scheduled proactive triggers. Not random — context-driven.

**Examples:**
- "Been a while since [user] messaged. Koroki sends a short message based on last topic."
- "User mentioned a game last week. Koroki brings it up: 'did you finish that?'"
- "Koroki has a 'mood' that shifts even when not in conversation. When user comes online,
  her state is based on what she's been 'thinking about'."

**How:**
- Orchestrator has a background task that checks per-user last-seen + last-topic every N hours
- If conditions met → generate a proactive message from Koroki's current mood state
- Mood state evolves slowly even offline (not random — weighted drift toward Koroki's base character)

**Inspired by:** AlphaEvolve's self-operating loop, basic human social behavior.

**Priority: MEDIUM** — high humanization value, requires careful design so it's not annoying.

---

### 5. Reflection Pass (Pre-send Review)

**Problem:** Koroki sometimes says things that feel slightly off — wrong tone, slightly
too assistant-like, or inconsistent with her current emotion. No review step exists.

**Idea:** Before sending, Koroki's response goes through a brief self-check:
"Does this sound like me? Does it match my current state?"

**How:**
- After Brain generates, a second short prompt evaluates: "Given Koroki's current state
  (emotion vector, relationship score, conversation history), does this response fit?
  Rate 1-5 and briefly explain if < 4."
- If score < 4, optionally regenerate with the critique as additional context.
- Can use the same Qwen3-8B (just a second short call) or a fast small model.

**Inspired by:** Looped LMs (#5) — iterative refinement in latent space before output.

**Priority: LOW-MEDIUM** — adds latency; only worth it for high-stakes responses (owner/high-relationship users).

---

### 6. Weighted Conversation Memory

**Problem:** Memory is flat. A message from 6 months ago has the same weight as one from
yesterday. A person would remember emotionally significant exchanges more vividly.

**Idea:** Emotion-weighted memory retrieval. When constructing context, weight recent
high-emotion exchanges higher. "That argument they had" or "the time she said something
really nice" surfaces more readily than mundane chatter.

**How:**
- Tag each memory entry with an emotion intensity score (already computable from emotion engine)
- Retrieval = recency + emotional intensity, not just recency
- Optionally decay emotional weight over time (old drama fades)

**Inspired by:** Attention Residuals (#4) — learned selective aggregation, not flat accumulation.

**Priority: LOW** — current memory system is already pretty good; this is refinement.

---

### 7. Adaptive Reasoning Mode

**Problem:** Koroki uses the same generation depth for "hi" and for "what's your view on
consciousness?" Overkill for trivial messages, underdone for complex ones.

**Idea:** Detect message complexity and route to thinking mode (Qwen3's /think) only when
warranted. Saves latency on casual exchanges, uses full reasoning for depth.

**How:** Simple classifier on input length + topic keywords. Short/casual → skip think.
Philosophical/emotional/complex → enable think. Already have the Qwen3 infrastructure.

**Inspired by:** DeepSeek V4 (#3) dual reasoning modes.

**Priority: LOW** — nice-to-have, marginal win. Qwen3 fast mode already handles most of this.

---

## Priority Order

| Priority | Feature | Status |
|----------|---------|--------|
| HIGH | Inter-service feedback loops | ✅ DONE — preference_logger.py, emotion divergence logging, /v1/preference API |
| HIGH | DPO preference data collection | ✅ DONE — Discord 👍/👎 reactions → set_preference() → labels.json |
| MEDIUM-HIGH | Emotion vector propagation | ✅ DONE — full 10-dim affect vector + slow mood baseline + private feelings in Brain context |
| MEDIUM | Proactive behavior | ✅ DONE (was already built) — autonomy_scheduler + proactive_poller + organic_outreach_task |
| MEDIUM | DPO training run | PENDING — run after 200+ preference pairs accumulate; use unsloth |
| LOW-MEDIUM | Reflection pass | ✅ DONE — rule-based: sycophant check, length/emotion mismatch, owner warmth, intimacy mismatch |
| LOW | Weighted memory | ✅ DONE — irritation+attachment boost in _salience_score; guaranteed high-emotion episode slot in RAG |
| LOW | Adaptive reasoning | ✅ DONE — _should_enable_thinking() routes complex/deep messages to Qwen3 think mode |

---

## Notes

- All features must be zero-cost (no paid APIs, no cloud GPU).
- DPO training uses unsloth on local GPU — same machine as inference, just not simultaneously.
- Proactive behavior requires careful rate-limiting so Koroki doesn't spam.
- The feedback loop (Feature 1) should be built first — it makes everything else debuggable.
