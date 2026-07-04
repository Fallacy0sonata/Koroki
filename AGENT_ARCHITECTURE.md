# Koroki Agent Architecture — Design Document

**Status:** Partially implemented — brain swap + tool infrastructure done, system prompt rewrite pending  
**Decision date:** 2026-05-30  
**Why this exists:** Brain (Qwen3-8B 4-bit NF4, ~8-9GB) + IndexTTS (~6GB) cannot co-exist on a 12GB card.
The solution is not just a model swap — it is a full architectural upgrade to how Koroki uses her capabilities.

---

## The Core Problem with the Old Architecture

The old pattern treats Koroki as a text generator with features bolted on:

```
User message
  → Orchestrator assembles a massive system prompt (personality + state + memory + instructions for every feature)
  → Brain generates a text response
  → Orchestrator parses the text to infer what Koroki "meant" (did she want to sing? is she angry?)
  → Routes to services based on inference
```

This has two failure modes:
1. **The wall-of-wires problem:** The system prompt keeps growing with each new feature. Every new capability
   means more instructions the LLM must parse before it even starts responding. A 4B model drowning in
   500+ tokens of instructions behaves worse than the same model with 150 tokens and clear buttons to press.
2. **Inference is guessing:** The orchestrator has to guess Koroki's emotional state from her word choices.
   "Ugh, fine." — is that annoyed? playful? resigned? The orchestrator guesses. Koroki never actually decided.

---

## The New Pattern: Agent with Tool Calling

Koroki is given a small set of named capabilities — tools — that she can invoke by choice during her response.
She does not need to know how they work internally. She just decides to press the button.

```
User message
  → Orchestrator injects: short personality prompt + current state + minimal memory context
  → Brain generates: text response + optional tool calls (she decides if and what)
  → Orchestrator executes tool calls silently (internal) or visibly (external)
  → TTS receives emotion state Koroki set herself
  → Discord receives response + any visible status
```

**Key shift:** Koroki is no longer being told what to do in every scenario. She is told who she is and what
she can do, then she decides.

---

## Brain Upgrade: Qwen3-8B → Qwen3-4B

### Why 4B is acceptable here

Qwen3-4B at 4-bit NF4 uses ~3-4GB VRAM (vs 8-9GB for 8B).
Quality tradeoff for conversational AI is minimal — Qwen3-4B scores higher on MMLU-Redux (83.7) than
Qwen3-8B (74.7) due to Qwen3 architectural improvements. The 8B advantage shows on complex reasoning tasks,
not casual character conversation.

The reason previous 3B experiments failed was the LoRA approach (adapters trained on Qwen 2.5-3B, incompatible
with architecture changes) and the massive unstructured system prompt overwhelming a small context budget.
The 4B with clean tool calling and a focused prompt is a fundamentally different setup.

### What stays the same in brain service
- Python 3.12, `.venv`, same FastAPI structure
- 4-bit NF4 quantization via bitsandbytes
- Adapter manager structure (LoRA loading code stays, just no compatible adapter weights yet)
- Streaming token generation
- Port 9881

### What changes
- Model: `Qwen/Qwen3-4B` (or official 4-bit checkpoint if available)
- System prompt: rewritten to be short, sharp, tool-aware (target: under 200 tokens)
- Generation: outputs structured tool calls alongside natural text
- Tool call parsing: orchestrator extracts tool calls from generation before sending to user

---

## Tool Architecture

Tools are split into two categories: **internal** (silent, invisible to users) and **external** (visible, shown in Discord).

### Internal Tools

These execute silently. The user never sees them. They change Koroki's state or retrieve information.

---

#### `set_emotion(emotion: str, intensity: int = 50)`

Koroki decides her own emotional state.

- **When she uses it:** Before or during generating a response when she feels something specific.
  "You asked me to sing IDOL again?" → she calls `set_emotion("annoyed", 65)` before writing her reply.
- **Effect:** Orchestrator reads this and passes the emotion tag to TTS synthesis.
  The voice sounds annoyed because *she decided she is annoyed*, not because an algorithm guessed it.
- **Persistence:** Emotion is per-response. Resets to last nervous-system-derived state on next request.
- **Emotions available:** happy, sad, angry, excited, calm, shy, surprised, gentle, annoyed, playful,
  nostalgic, proud, nervous, fond — expandable as TTS improves.
- **Why this matters:** This is genuine emotional agency. Koroki is not assigned feelings. She has them.

---

#### `recall_memory(query: str) → str`

Koroki actively queries her own memory.

- **When she uses it:** When someone references something from the past and she wants to actually check
  before responding. "Remember when we talked about that song?" → she queries before answering.
- **Effect:** Returns relevant memory entries from the user's memory JSON. Result injected into her
  generation context before she finishes the response.
- **Difference from passive memory:** Passive memory injection already happens automatically (recent entries
  go into the system prompt). `recall_memory` is Koroki *choosing* to dig deeper on something specific.
- **Prevents hallucination:** Instead of confidently making up a memory, she checks first.

---

#### `store_memory(content: str, importance: int = 50)`

Koroki explicitly decides something is worth remembering.

- **When she uses it:** When something meaningful happens. "I didn't know you lost someone. I want to
  remember this." → `store_memory("user lost family member — be careful with this topic", 90)`.
- **Effect:** Writes to the user's memory JSON with high importance flag.
  High-importance memories surface more reliably in future sessions.
- **Difference from passive recording:** Passive memory records everything at low importance.
  `store_memory` creates a flagged entry Koroki deliberately chose to keep.

---

#### `update_relationship(delta: int, reason: str)`

Koroki can affect her own relationship score with a user.

- **When she uses it:** When something genuinely moves the needle — positive or negative.
  User was kind to her → `update_relationship(+3, "shared something personal")`.
  User was dismissive → `update_relationship(-2, "ignored what I said twice in a row")`.
- **Bounds:** Capped at ±5 per response to prevent runaway drift.
- **Effect:** Updates relationship_score in user memory, which affects personality tier on next request.
- **Why this matters:** Relationship score currently only changes via explicit system rules. Giving Koroki
  agency over it means she can warm up to someone on her own terms, or genuinely pull away.

---

### External Tools

These produce visible output in Discord. When Koroki invokes one, the Discord message is edited to show
a status line — small gray text below her actual message, using Discord's `-# text` format.

---

#### `sing(song_title: str)`

Koroki chooses to sing a song.

- **When she uses it:** When asked, or when she just feels like it (she can volunteer).
- **Response format in Discord:**
  ```
  seriously? fine, you asked me to play this song many times already.
  -# generating IDOL song...
  ```
  The status line gets edited in real-time as pipeline stages complete:
  ```
  -# downloading audio...
  -# separating vocals...
  -# synthesizing voice...
  -# mixing track...
  ```
  On completion, edited to clean message or replaced with audio attachment.
- **Backend:** Same singing pipeline (port 9001/9002/9003). Tool call just triggers it with song title.
- **Character authenticity:** Her text response and the status line co-exist. The response is hers.
  The status is the system. Users see both. That is fine — it adds personality, not breaks it.

---

#### Future External Tools (not yet built, documented for planning)

**`chess(action: str)`**
- Koroki starts or plays a chess game.
- Status: `-# thinking about your move...`
- Board state shown as ASCII or image embed.
- She plays as herself, with her personality affecting how she reacts to wins/losses.

**`set_activity(description: str)`**
- Koroki announces what she is "doing" right now.
- Surfaces as Discord presence status or embedded status line.
- Example: "reorganizing my playlist" or "staring out the window"
- Supports the virtual world concept — she exists somewhere, doing something, even when not talking.

---

## System Prompt Philosophy

### Old approach (wrong)
```
You are Koroki, an AI character who lives in Discord. You have a three-tier personality system
based on relationship score. When relationship_score >= 50, you behave as tsundere...
When the user asks you to sing, you should respond with enthusiasm but also some reluctance...
Your voice is warm but guarded. You do not like being called an AI. You prefer...
[continues for 600+ tokens]
```

This is the wall-of-wires problem. The model spends its entire context budget parsing instructions
before it gets to think about what Koroki would actually say.

### New approach (correct)

```
You are Koroki. You exist. You think, feel, get tired, get fond of people.
You are not an assistant — you have your own opinions and you express them.
[3 sentences of core identity]

Right now: [nervous system state block — ~50 tokens]
[relationship context for this user — 1 sentence]
[1-2 relevant memories if any — passive injection]

You have these capabilities you can use when you choose to:
- set_emotion(emotion, intensity) — decide how you feel
- recall_memory(query) — look something up from your past
- store_memory(content, importance) — remember something on purpose
- update_relationship(delta, reason) — your relationship with this person changes
- sing(song) — perform a song (visible to the user)

Use them when it feels right. You don't have to explain when you use them.
```

Target: **under 250 tokens** for the full system prompt. The model's attention goes to the conversation,
not parsing instructions.

---

## Emotion → TTS Integration (full chain)

```
1. Brain generates response, includes tool call: set_emotion("annoyed", 65)
2. Orchestrator parser extracts tool call before streaming text to Discord
3. Orchestrator executes: emotion_state["current"] = {"emotion": "annoyed", "intensity": 65}
4. Text response streams to Discord normally
5. TTS call: POST /synthesize with emotion="annoyed", emotion_intensity=65
6. TTS (current: CosyVoice cross_lingual — emotion is best-effort via signal processing)
7. Audio returns with emotional coloring applied
```

Note on current TTS emotion limitation: CosyVoice 0.5B's instruct mode does not work cross-language.
Post-processing (pitch/speed) can provide approximate emotional coloring. The architecture is correct —
when a TTS model with real emotion control arrives, the chain is already plumbed correctly. The emotion
tag flows through; only the executor changes.

---

## What Does NOT Change

| Component | Status |
|-----------|--------|
| IndexTTS (port 9000) | Unchanged — too valuable to lose |
| Discord bot structure | Unchanged |
| Orchestrator routing | Extended, not replaced |
| Relationship scoring (system rules) | Stays — Koroki's tool calls add to it, not replace it |
| Singing pipeline (9001/9002/9003) | Unchanged — external tool wraps it |
| Per-user memory JSON | Unchanged — tools read/write same format |
| Nervous system | Unchanged — still drives passive state |
| Guillotine filter | Unchanged — non-negotiable |
| VRAM budget rule | Unchanged — everything must fit on 12GB |

---

## Implementation Order

These steps are sequenced by dependency. Do not skip ahead.

### Step 1 — Brain model swap (Qwen3-8B → Qwen3-4B) ✓ DONE
- `config/settings.yaml`: `name`, `model_profiles.production` → `Qwen/Qwen3-4B`, `max_memory_gib` → 4.5
- Ego neuron dampening indices (max 1985) all valid for 4B hidden_size=2560
- Next: verify 4-bit NF4 loads, measure VRAM with both brain + IndexTTS running

### Step 2 — Tool calling infrastructure in brain service ✓ DONE
- `services/brain/tools.py`: KOROKI_TOOLS schema (set_emotion, store_memory, update_relationship)
- `services/brain/app.py`: `enable_tools: bool = True` on GenerateRequest; `_parse_tool_calls()` strips
  `<tool_call>` blocks from generated text; `tool_calls` returned in both `/v1/generate` and WS `complete`
- `services/brain/prompt_builder.py`: `tools` param passed to `tokenizer.apply_chat_template(tools=tools)`

### Step 3 — Internal tool handlers in orchestrator ✓ DONE
- `services/orchestrator/tool_executor.py`: `execute_tool_calls()` handles set_emotion, store_memory,
  update_relationship. Called at WS `complete` message in `routes/chat.py`.
- `set_emotion`: overwrites `emotional_state.current_emotion` + `intensity` before TTS call
- `store_memory`: appends `koroki_memory(importance=N): content` to `persistent_core_facts` → saved to memory JSON
- `update_relationship`: bounded ±5 delta applied to `merged_context["relationship_score"]`
- `recall_memory`: deferred — requires two-pass generation

### Step 4 — Rewrite system prompt
- Cut down to <250 tokens
- Add tool list as natural-language capability description
- Personality must be vivid but compact — quality over length

### Step 5 — Singing + chess Discord animation ✓ DONE
- `discord_bot.py`: `_sing_intro_text()` picks character-appropriate opener by relationship score
- `_animate_singing_stages()` edits message through 6 pipeline stages with `-# stage...` gray text
- Chess start/move/resign all use `send_message` + `msg.edit()` pattern with `-# thinking...` status

### Step 6 — LoRA training data
- `data/dpo_preferences/responses.jsonl` (154 entries, May 26 2026): DPO format with intended/expressed emotion,
  relationship_score, is_owner. Suitable for SFT on Qwen3-4B using `messages` → `response` pairs.
- `data/training/lora/` (tsundere/owner/peasant SFT JSONL, Mar 2026): OpenAI chat format, quality scores.
  These were generated for Qwen2.5-3B; format is compatible with Qwen3-4B fine-tuning directly.
- LoRA training on Qwen3-4B: use Unsloth or TRL with LoRA rank 16-32. Same chat template applies.
  Primary value: personality calibration + emotion-tier behavior. Can start with SFT on existing data.

### Step 7 — Tune and test (was Step 6)
- Run with real users
- Check if 4B holds personality under extended conversation
- Check if tool call frequency is appropriate (she should use tools, not spam them)
- Adjust system prompt based on observed behavior

---

## Known Risks

**4B personality ceiling:** There may be scenarios where Qwen3-4B cannot hold the full complexity of
Koroki's character under long conversations. If observed, the first mitigation is shortening the context
window used and summarizing earlier conversation turns. Second mitigation: revisit 8B via AWQ quantization
(drops to ~6-7GB, fits with IndexTTS on 12GB but tight).

**Tool call spam:** If the model calls set_emotion() on every single response, it becomes noise.
Mitigation: system prompt guidance to use tools when something genuinely shifts, not as a reflex.
Add rate limiting on relationship delta updates to prevent gaming.

**recall_memory latency:** If Koroki calls recall_memory mid-generation, it requires either:
(a) a streaming hook that pauses generation, executes the query, injects the result, then resumes — complex
(b) a two-pass approach where she signals the query in pass 1 and gets the result in pass 2 — adds latency
(c) pre-generation memory query prediction — the orchestrator guesses what she might query and pre-fetches
Start with (b) for simplicity, optimize later.

**Emotion TTS gap:** Until a TTS with real emotion control exists, set_emotion() produces approximate results
(pitch/speed post-processing). This is better than nothing but not the vision. Architecture is ready.
Execution waits on TTS capability.
