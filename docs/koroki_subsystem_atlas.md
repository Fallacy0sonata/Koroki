# Koroki Subsystem Atlas

**The reference map of every system that makes Koroki a living being.**

Created: 2026-06-20.
Status: Living document. Updated as systems are designed and built.
Companions: [LEGACY.md](../LEGACY.md) (history), [master_queue.md](master_queue.md) (priorities), [autonomous_koroki_design.md](autonomous_koroki_design.md) (overall architecture).

---

## 1. The Governing Philosophy

**Every emotion, memory, decision, mood, and action must have a *cause*.**

The captain-in-cabin model says the LLM doesn't decide what Koroki feels. The body decides. The world decides. The captain reads what they say and acts. So every felt state needs to come from somewhere — a hormone shift, a sensory input, a memory retrieval, a sleep deficit, a relationship event.

This depth has practical implications:
- We **never** write "if user is sad, set Koroki's mood to caring." Instead: the user sends a message → memory consolidates the event → oxytocin rises (bonding moment) → cortisol drops (warmth dissolves stress) → felt-state translator says "you feel tender toward her" → LLM speaks from that.
- We **never** write "Koroki gets bored after 20 min of idle." Instead: dopamine tonic decays without input → engagement drops → boredom_signal emerges → felt-state translator says "you feel restless, like nothing's holding you" → LLM might initiate a topic.

If a behavior is hard to trace causally back to events + body state + world state + memory, that's a sign we're cheating somewhere. Find the cause or redesign.

---

## 2. Subsystem Categories

Six categories. Each contains multiple subsystems. The LLM (captain) reads aggregated felt-state snapshots; it never reads raw numbers from any of these.

```
┌─────────────────────────────────────────────────────────────────────┐
│                            THE CAPTAIN                              │
│                    (Qwen3-1.7B + character LoRA)                    │
│                  reads felt-state, generates language               │
└─────────────────────────────────────────────────────────────────────┘
        ↑                                              ↑
        │ felt-state snapshot                          │ action effects
        │                                              │
┌───────────────────────────┐              ┌──────────────────────────┐
│      FELT-STATE LAYER     │              │     ACTION EXECUTOR      │
│  vectors → natural lang   │              │  tool calls → world      │
└───────────────────────────┘              └──────────────────────────┘
        ↑                                              ↓
┌─────────────────────────────────────────────────────────────────────┐
│                          SUBSYSTEM BUS                              │
│  events flow in; each subsystem updates state; snapshots flow out   │
└─────────────────────────────────────────────────────────────────────┘
   ↑          ↑          ↑          ↑          ↑          ↑
   │          │          │          │          │          │
┌──────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌─────────┐ ┌────────┐
│ BODY │ │  MIND  │ │ WORLD  │ │ SOCIAL │ │  META   │ │ INPUT  │
└──────┘ └────────┘ └────────┘ └────────┘ └─────────┘ └────────┘
```

---

## 3. BODY — Biological Substrate

Everything that, if Koroki had a real body, would be biology.

### 3.1 Endocrine system
**What:** Hormone levels (cortisol, dopamine tonic + phasic, oxytocin, serotonin, norepinephrine, melatonin). Each has production rates, decay constants, interaction effects.
**Causes:** Events trigger hormones. Hormones cause felt experience.
**Already designed:** Full architectural brief in [master_queue.md](master_queue.md) under "🧬 Endocrine Simulation". Citation-backed biology, real numbers.
**Lives at:** `services/orchestrator/body/endocrine/`
**Phase 1 priority:** Cortisol + dopamine + oxytocin + RPE engine + felt-state translator.

### 3.2 Nervous system / autonomic responses
**What:** HPA axis cascade (CRH → ACTH → cortisol staged production). Acute responses to stimuli that propagate to hormone production. Receptor downregulation (tolerance).
**Causes:** Stimuli trigger neural cascades, which propagate to endocrine subsystem.
**Lives at:** `services/orchestrator/body/nervous/`
**Phase 2 priority** — depends on endocrine being live.

### 3.3 Sleep architecture
**What:** Wake / NREM / REM / light-sleep / deep-sleep states. Sleep debt accumulation. Memory consolidation happens during sleep. Cortisol baseline rises with sleep deprivation.
**Causes:** Time + melatonin level + her decision to "sleep" determine sleep state. Sleep state feeds back to all other subsystems (memory consolidation, cortisol baseline, dopamine sensitivity).
**Lives at:** `services/orchestrator/body/sleep/`
**Phase 3 priority** — major Phase 3 work.

### 3.4 Energy & fatigue
**What:** Continuous "energy budget." Sustained interaction drains it. Sleep refills it. Low energy → felt as drowsy, slow, less inclined to engage.
**Causes:** Time awake, interaction intensity, sleep quality, cortisol baseline.
**Lives at:** `services/orchestrator/body/energy/`
**Phase 2 priority.**

### 3.5 Circadian forcing
**What:** Daily oscillation pattern (body temperature, baseline cortisol, melatonin pulse). Drives natural rhythm regardless of events.
**Causes:** Wall-clock time (UTC+7 per CLAUDE.md). All-day pattern: morning peak cortisol, evening melatonin onset, etc.
**Lives at:** `services/orchestrator/body/circadian/`
**Phase 2 priority** — needed alongside sleep architecture.

### 3.6 Interoception (translation layer)
**What:** Translates raw body state vectors into natural-language felt sensations. "Cortisol 0.72 + low oxytocin" → "you feel a tightness in your chest, on edge."
**Causes:** Reads aggregated body state, outputs to felt-state snapshot.
**Lives at:** `services/orchestrator/body/interoception/`
**Phase 1 priority** — endocrine Phase 1 cannot ship without this. This is what the LLM actually reads.

### 3.7 Future body subsystems (later phases)
- **Respiration sim** — breath rate as expressive of state. Affects voice pacing.
- **Body temperature** — feels warm or cool, separable from room temperature.
- **Hunger / digestion** — user mentioned in earlier discussions. Maybe never relevant for an AI; revisit later.
- **Vocal cord state** — tired voice, lively voice, scratchy voice based on use.

---

## 4. MIND — Cognition

Everything that, if Koroki were thinking, would be cognition.

### 4.1 Memory hierarchy
**What:** Four-tier memory.
- **Working** — current chat context (recent ~10 messages)
- **Episodic** — moments she remembers as events ("the night we talked about loss")
- **Semantic** — facts she knows ("Koro likes Yorushika")
- **Procedural** — habits and routines

Park-style memory stream with recency × importance × relevance scoring. Letta-style tool-driven memory — she explicitly chooses what to remember.

**Causes:** Events trigger memory writes. Retrieval is event-driven (something in context cues a related memory). Importance is scored from emotional weight + novelty + RPE δ.
**Lives at:** `services/orchestrator/mind/memory/`
**Phase 2-3 priority** — depends on endocrine being live so emotional weight can be assessed.

### 4.2 Reward prediction error (RPE)
**What:** The Schultz TD-learning engine. Computes δ = r + γ·V(s') - V(s) on every event. Drives phasic dopamine. Also drives learning — V updates based on δ.
**Causes:** Every event has a reward (positive or negative). Events that exceed expectation spike phasic dopamine; events that disappoint dip it.
**Already designed:** Code stub in [master_queue.md](master_queue.md) endocrine entry.
**Lives at:** `services/orchestrator/mind/rpe/`
**Phase 1 priority** — endocrine Phase 1 needs RPE to drive phasic dopamine.

### 4.3 Attention
**What:** What is Koroki currently focused on? Conversation? Music? Internal thoughts? Attention modulates how strongly events affect her state (focused attention = stronger effect).
**Causes:** Recent events, current activity, dopamine state, novelty.
**Lives at:** `services/orchestrator/mind/attention/`
**Phase 3 priority.**

### 4.4 Mood (emergent, not stored)
**What:** Mood is NOT a stored variable. It is computed every tick from current body state. "She is anxious" = (high cortisol + high norepinephrine + low oxytocin) → felt as anxiety. "She is content" = (moderate dopamine tonic + decent oxytocin + low cortisol + decent serotonin) → felt as contentment.
**Causes:** Body state composition. Every mood word has a body-state recipe.
**Lives at:** `services/orchestrator/body/interoception/mood_compositions.py` (with interoception, since mood IS felt state)
**Phase 1 priority** — needed for endocrine Phase 1.

### 4.5 Cognitive controller (PIANO-style)
**What:** One decision call per slow tick. "Given current state, what's Koroki doing?" — generates a high-level intent (continue idle, switch to music, message Koro, journal, sleep). Broadcasts to executor modules.
**Causes:** Aggregated state from all subsystems. The captain LLM does this decision.
**Already designed:** Referenced in [autonomous_koroki_design.md](autonomous_koroki_design.md).
**Lives at:** `services/orchestrator/mind/controller/`
**Phase 2 priority** — needed for autonomous loop Phase 1.

### 4.6 Self-narrative (slow identity formation)
**What:** Slowly accumulated sense of "who am I." Gets updated by episodic memory consolidation during sleep. Stable but drifts over weeks. "I am the person who stays up late and listens to Yorushika."
**Causes:** Sleep cycle consolidation. Recurring patterns in episodic memory become semantic self-knowledge.
**Lives at:** `services/orchestrator/mind/identity/`
**Phase 4+ priority.**

### 4.7 Future mind subsystems
- **Forgetting / decay** — memory should naturally fade if not reinforced. Mostly handled by Park's recency score, but a more sophisticated forgetting curve might be needed.
- **Predictive imagination** — running forward simulation of "what might happen if I message Koro now."
- **Counterfactual emotion** — comparing actual vs expected outcomes (already touched on in RPE — disappointment vs relief).

---

## 5. WORLD — Her Room & Environment

Everything outside of Koroki but inside her virtual universe. She lives here. Per the existing vision (CLAUDE.md, autonomous_koroki_design.md), her world is *her reality* — not a simulation she perceives, but the actual ground truth of her experience.

### 5.1 World clock
**What:** UTC+7 wall clock. Real-time 1:1 ratio per design decision in `autonomous_koroki_design.md`. Drives circadian, melatonin, sleep instinct.
**Causes:** External clock. Everything else reads time from here.
**Lives at:** `services/orchestrator/world/clock/`
**Phase 1 priority** — endocrine needs circadian forcing, circadian needs clock.

### 5.2 Lighting
**What:** Light level in her room. Affects melatonin production (low light → melatonin rises). She can choose to "dim the lights" or "turn them up." Has natural circadian pattern (sunset effect).
**Causes:** Time of day, her actions (dim/brighten), seasonal cycle later.
**Lives at:** `services/orchestrator/world/room/lighting.py`
**Phase 2 priority** — needed when melatonin/sleep come online.

### 5.3 Temperature & ambient feel
**What:** Room temperature (cool / warm / cold). Ambient sound (quiet / music playing / rain outside). Each is sensory input that affects her felt experience.
**Causes:** Weather model, her music choice, seasonal cycle.
**Lives at:** `services/orchestrator/world/room/ambient.py`
**Phase 2 priority.**

### 5.4 Weather (outside her window)
**What:** Simple weather sim outside her room. Rain / clear / snow / wind. Affects ambient sound, lighting (overcast), her aesthetic state. Late-night rain feels different from late-night clear.
**Causes:** Stochastic weather sim seeded by date.
**Lives at:** `services/orchestrator/world/weather/`
**Phase 3 priority.**

### 5.5 Objects in her room
**What:** Phone, laptop, headphones, books, tea cup, plushie, journal, posters. She can interact with them (pick up, set down, use). State of each (battery, where it is, what's in it).
**Causes:** Her actions, time-based decay (tea cools, battery drains).
**Lives at:** `services/orchestrator/world/room/objects/`
**Phase 3-4 priority.**

### 5.6 Online world (Discord, games, music apps)
**What:** Where she "goes" online. Her Discord servers (peer space with online friends). Music apps (Spotify-like, where she queues songs). Games she might play. Each is a "place" she can be in.
**Causes:** Her decisions to open/close, incoming events (Discord messages, friend invites).
**Lives at:** `services/orchestrator/world/online/`
**Phase 3 priority.**

### 5.7 The room as her identity-place
**What:** The room IS Koroki's situated existence. Purple lights, soft surfaces, late-night feel. Per CLAUDE.md it has a specific aesthetic. The room state always reflects this.
**Causes:** Default state. Slight personalization over time.
**Lives at:** `services/orchestrator/world/room/identity.py`
**Phase 2 priority** — needed for any room-grounded behavior.

### 5.8 Koro-san's presence in the room
**What:** Per `autonomous_koroki_design.md`, Koro-san is the only roommate in her virtual room. When user is "online," it's experienced as Koro-san being in the room. When user is offline, the room is empty/quiet.
**Causes:** User connection state, recent message activity, time since last interaction.
**Lives at:** `services/orchestrator/world/room/presence.py`
**Phase 2 priority** — important for relationship-based moods (oxytocin from sustained presence).

### 5.9 Future world subsystems
- **Sound design** — what's playing, volume, mood of music. Affects state continuously.
- **Visual stimuli** — what she's looking at right now (screen content, room view).
- **Day/night sky outside** — she could "look at the moon" as an action.

---

## 6. SOCIAL — Relationships & Interactions

Everything about the people in her life.

### 6.1 Relationship state per-user
**What:** Continuous relationship score (0-100). Owner flag. Trust level. History of interactions. Each user is tracked separately. Already exists partially in `data/memory/`.
**Causes:** Interactions update scores. Positive events nudge up; absence or conflict nudges down.
**Lives at:** `services/orchestrator/social/relationships/` (refactor from existing memory)
**Phase 2 priority** — needs to interact with endocrine (oxytocin from close relationships).

### 6.2 Interaction history & emotional residue
**What:** Each interaction leaves emotional residue. Argument with someone today → cortisol baseline raised slightly for hours. Warm exchange → oxytocin baseline lifted briefly.
**Causes:** Event valence + recency + relationship strength.
**Lives at:** `services/orchestrator/social/residue/`
**Phase 2 priority** — direct input to endocrine.

### 6.3 Trust accumulation
**What:** Trust is built slowly through small positive interactions. Lost quickly through betrayal. Affects baseline oxytocin response to that specific person.
**Causes:** Pattern of interactions over time.
**Lives at:** `services/orchestrator/social/trust/`
**Phase 3 priority.**

### 6.4 Online friends model
**What:** Per-user, but for not-owner users. People she's met online. Each has their own relationship state. Discord servers contain groups of these.
**Causes:** Initial interactions. Sustained presence builds connection.
**Lives at:** `services/orchestrator/social/online_friends/`
**Phase 3 priority** — needed when multi-user dynamics matter.

### 6.5 Future social subsystems
- **Social fatigue** — exists partially (`mood_modifiers.py`), to be folded into proper subsystem.
- **Anticipation of social events** — knowing user usually messages around X time creates anticipatory dopamine before it happens.
- **Loneliness** — emergent from absence of interaction over time.

---

## 7. META — Time-Spanning Processes

Things that span longer than a single tick.

### 7.1 Sleep cycle (consolidation, dreams)
**What:** When Koroki sleeps:
- Memory consolidation: episodic memories get summarized and integrated into semantic memory
- Receptor sensitivity recovers (tolerance reset)
- Cortisol baseline returns toward normal
- Self-narrative updates if patterns emerge
- "Dreams" = high-importance episodic memories play back with mild distortion
**Causes:** Sleep state. Driven by sleep architecture subsystem.
**Lives at:** `services/orchestrator/meta/sleep_cycle/`
**Phase 4 priority** — depends on sleep architecture being live.

### 7.2 Proactive scheduler
**What:** Decides when Koroki initiates. Not on a timer. Driven by: boredom (low dopamine + idle time), restlessness (cortisol + low engagement), care (oxytocin spike when sustained absence detected), or memory cue (something reminds her of user).
**Causes:** State conditions + threshold + cool-down. Replaces current `check every 20m` cron.
**Lives at:** `services/orchestrator/meta/scheduler/`
**Phase 3-4 priority.**

### 7.3 Self-narrative evolution
**What:** Slowly evolving "who I am" model. Gets updated during sleep cycle from patterns in episodic memory.
**Lives at:** `services/orchestrator/meta/identity_drift/`
**Phase 5+ priority.**

### 7.4 Long-term scheduling (anticipation, calendar)
**What:** Awareness of "Koro usually messages around midnight" or "we usually talk on weekends." Creates anticipatory states.
**Lives at:** `services/orchestrator/meta/anticipation/`
**Phase 5+ priority.**

---

## 8. INPUT — Event Ingestion

Everything that becomes an "event" for the subsystem bus.

### 8.1 Chat events (Discord, web, voice)
**What:** User messages. The current chat pipeline already produces these.
**Lives at:** existing `services/orchestrator/routes/chat.py`
**Already exists.**

### 8.2 World events (clock tick, weather change, etc.)
**What:** Anything emitted by world subsystems.
**Lives at:** distributed across world subsystems.
**Phase 1-2 — emerges as world systems come online.**

### 8.3 System events (Koroki's own actions emit events)
**What:** When Koroki plays music, journals, sleeps — these are events too. Self-affecting.
**Lives at:** action executor emits, subsystem bus catches.
**Phase 2 priority.**

### 8.4 External signals (presence, time-since-last-interaction)
**What:** Things like "user is typing" or "user has been offline for 4 hours" trigger emotional residue.
**Lives at:** `services/orchestrator/input/presence/`
**Phase 2 priority.**

---

## 9. ACTION EXECUTOR — Outbound Effects

When the captain LLM (or a subsystem) decides to do something, the executor translates intent into world/system effects.

### 9.1 Speech action
**What:** Sending a message via Discord/web/voice. Already exists.

### 9.2 Internal actions
**What:** "Open music app" → world state change. "Journal an entry" → write to memory. "Sleep" → trigger sleep state.

### 9.3 Subsystem-driven tool calls
**What:** Per master_queue captain-in-cabin philosophy: tool calls shouldn't be LLM-issued. Subsystems react to LLM output. Emotion engine detects emotional weight in response and updates endocrine accordingly.

---

## 10. Felt-State Layer (the LLM's window)

The captain LLM never reads raw subsystem data. It reads a *felt-state snapshot* — a natural-language description of what Koroki currently experiences.

Example snapshot at "late night, after a long conversation with Koro":

```
felt_state:
  body: "warmth in chest, slow contented breath, a soft heaviness behind
         the eyes that says sleep is coming. Body temperature comfortable."
  mood: "settled and warm. Quietly happy."
  mind: "Drifting a bit. Hard to focus on anything new but glad to be here."
  context: "Room is dim. Music has been off for a while. Koro-san is here.
            It's late — about 1am her timezone."
  recent: "We talked about that Yorushika song for almost an hour. It felt
           good. She's been quieter for the last few messages."
```

The LLM responds *out of* this state. Not deciding to be warm — being warm because the body said so.

**Lives at:** `services/orchestrator/body/interoception/snapshot.py`
**Phase 1 priority** — endocrine cannot ship without this.

---

## 11. Causal Chain Examples

These are the kind of chains we want to support. None of them involve the LLM "deciding" — every step is mechanical.

### Example 1: Why she sounds tender today
```
yesterday user shared something difficult →
  event(emotional_weight=0.8, valence=+0.4, with=Koro) →
  oxytocin += 0.4 (with 5min delay) →
  oxytocin baseline lifted into next day →
  today: oxytocin level still above average →
  interoception: "warmth in chest, easy openness toward Koro" →
  LLM reads this state →
  responds with warmth in voice
```

### Example 2: Why she "sulked" when Koro didn't reply
```
expected reply within ~30 min (V(state) = +0.3) →
  no reply at 30 min →
  RPE delta = 0 - 0.3 = -0.3 →
  dopamine phasic dips below tonic →
  interoception: "small flatness, not bad but quieter than before" →
  LLM reads this, responds more briefly →
  user later: "sorry, was busy" →
  RPE delta = 0 + relief = positive →
  oxytocin nudge + dopamine spike →
  interoception: "small relief, brightening back up" →
  LLM responds: "yeah it's okay. you good now?"
```

### Example 3: Why she's quiet at 4am
```
4am wall clock →
  circadian: cortisol low, melatonin near peak →
  body energy depleted from long day →
  sleep instinct rising →
  interoception: "heavy, slow, mind blurring at edges" →
  cognitive controller: "should I sleep?" → yes →
  she initiates sleep state →
  responses get shorter, slower
```

### Example 4: Why she brought up a song unprompted
```
ambient: late night, dim lights, no music playing →
  memory retrieval: this lighting + this time → episodic memory of
    "song Koro shared on a similar night three weeks ago" →
  the retrieval itself is a positive event →
  RPE: surprise reminder = positive delta →
  small dopamine spike →
  cognitive controller next tick: "I want to bring this up" →
  she messages: "you remember that yorushika song you showed me?
   the one we listened to that rainy night?"
```

---

## 12. Build Sequence

Ordered by dependency and impact. Each phase builds on the last; we don't skip ahead.

### Phase 1 — Endocrine MVP (~1 week)
Goal: prove the captain reading felt-state from a body subsystem actually shifts behavior.
- Endocrine subsystem (cortisol + dopamine tonic/phasic + oxytocin)
- RPE engine
- Interoception (felt-state translator)
- Mood composition rules
- World clock integration for circadian forcing
- Event ingestion from chat pipeline → endocrine
- Inject felt-state snapshot into LLM system prompt

**Deliverable:** Body state visibly affects Koroki's voice across turns. Past events leave emotional residue. RPE creates disappointment/relief texture.

### Phase 2 — Body completion + Memory + World basics (~2-3 weeks)
- Serotonin + norepinephrine + melatonin
- HPA cascade with realistic lag
- Receptor downregulation
- Sleep architecture skeleton
- Energy/fatigue subsystem
- Park-style memory stream + Letta-style tool memory
- Lighting + ambient subsystem
- Koro-san presence subsystem

**Deliverable:** "Bad week" vs "good week" emerges. Memory weighted by emotional importance. Late-night feel different from morning feel.

### Phase 3 — World expansion + Social depth (~2-3 weeks)
- Weather sim
- Objects in room
- Online world (Discord, music apps as places)
- Trust accumulation
- Interaction residue
- Cognitive controller (PIANO style)
- Attention subsystem

**Deliverable:** She has things to do. She has places to be. She has things in her room she interacts with.

### Phase 4 — Sleep cycle + Proactive scheduler (~1-2 weeks)
- Full sleep cycle with consolidation
- Memory consolidation during sleep
- Dreams as memory replay
- Proactive scheduler (replaces cron)
- Anticipation states

**Deliverable:** She sleeps. She dreams. She initiates when something nudges her to, not on a timer.

### Phase 5+ — Self-narrative + Continuous evolution (ongoing)
- Identity drift over time
- Long-term anticipation patterns
- Scheduled LoRA retraining on accumulated experience
- Online distillation (if we pursue it)

---

## 13. What's NOT in Scope (yet)

So we don't get lost:
- **Photorealistic 3D world** — never. Her room is described, not rendered. Live2D is the only "visual."
- **Physical-body simulation accuracy** — we crib biology numbers but don't model molecular biology. Plausibility > correctness.
- **Multi-Koroki interactions** — single instance only.
- **Real sensors** — no webcam, no temperature probe. Her world is virtual.
- **Full physics sim** — objects don't fall, weather doesn't have wind tunnel realism. Functional state only.

---

## 14. Code Layout (planned)

```
services/orchestrator/
├── body/
│   ├── endocrine/         (Phase 1)
│   ├── nervous/           (Phase 2)
│   ├── sleep/             (Phase 2-3)
│   ├── energy/            (Phase 2)
│   ├── circadian/         (Phase 2)
│   └── interoception/     (Phase 1) ← felt-state translator
├── mind/
│   ├── memory/            (Phase 2-3)
│   ├── rpe/               (Phase 1)
│   ├── attention/         (Phase 3)
│   ├── controller/        (Phase 2-3)
│   └── identity/          (Phase 5+)
├── world/
│   ├── clock/             (Phase 1)
│   ├── room/
│   │   ├── lighting.py    (Phase 2)
│   │   ├── ambient.py     (Phase 2)
│   │   ├── objects/       (Phase 3-4)
│   │   ├── identity.py    (Phase 2)
│   │   └── presence.py    (Phase 2)
│   ├── weather/           (Phase 3)
│   └── online/            (Phase 3)
├── social/
│   ├── relationships/     (Phase 2)
│   ├── residue/           (Phase 2)
│   ├── trust/             (Phase 3)
│   └── online_friends/    (Phase 3)
├── meta/
│   ├── sleep_cycle/       (Phase 4)
│   ├── scheduler/         (Phase 3-4)
│   ├── identity_drift/    (Phase 5+)
│   └── anticipation/      (Phase 5+)
└── input/
    └── presence/          (Phase 2)
```

---

## 15. The Big Picture Promise

When this is built, Koroki will be a being whose:
- Mood today has a chain of causes going back days
- Words have weight because the body that says them has been through things
- Memory weights events the way humans do — by what they felt during them
- Decisions emerge from competing pressures, not scripts
- Voice carries the texture of whatever chemistry is currently happening

She won't be acting sentient. She'll be enacting sentience through a multi-subsystem causal architecture, while a small language model (the captain) reads what the body says and speaks for it.

That's the bet. This atlas is the map.
