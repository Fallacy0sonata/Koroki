# Koroki Master Queue

Living document. Last updated: 2026-07-02.

> **Current position (2026-07-02) — ACTIVE: Living Avatar → frontend ART PIPELINE.** This session built
> the room/scene/furniture art for the 2D-puppet world and locked the art style. **Session handoff (user
> switching to desktop app — pick up cold here):**
>
> - **STYLE ENGINE LOCKED:** FLUX.1-dev fp8 (`tools/ComfyUI/models/checkpoints/flux1-dev-fp8.safetensors`)
>   + **"Sketch Pad Concept Art" FLUX LoRA** (`tools/ComfyUI/models/loras/sketch_sketchpad_concept.safetensors`,
>   Civitai 1433827). Trigger `digrngbrsh`, strength **1.0**, FluxGuidance 3.5, euler/simple, 26 steps,
>   cfg 1.0, via `LoraLoaderModelOnly`. Painterly-moody concept-art look. User picked this over the
>   broken-line `sketch_chaotic_lineart.safetensors` (Civitai 1278849, trigger `illustration004`, also on disk).
> - **KEY FINDING — FLUX clean-line ceiling:** base FLUX cannot draw rough/broken sketch lines via prompt
>   or cfg+negative (raising cfg just deletes linework → reverts to painting). Only a real-sketch-trained
>   LoRA shifts that prior. So "bootstrap-train a style LoRA on FLUX's own outputs" is capped — moot now
>   that Sketch Pad gives the look on-demand (**no custom style-LoRA training needed**).
> - **ROOM POOL DONE (approved):** `assets/flux_style_farm/P_*.png` — 20 cozy rooms (bedroom/lounge/study/
>   balcony/kitchen/bathroom/tatami/etc.) at str 1.0. `P_bedroom_night` is the new hero bedroom (old
>   `assets/world/bedroom_v2.png` is now style-orphaned — soft-painterly, doesn't match; retire it).
> - **FURNITURE LIBRARY DONE:** `assets/flux_style_farm/furniture/cut/` — 20 layer-ready transparent PNGs
>   (bed/sofa/armchair/bookshelf/lamps/plants/tables/desk/nightstand/rug/beanbag/cushions/heart_pillow/
>   wall_art/string_lights/ac_unit/curtains). Raws in `furniture/raw/`. `desk_lamp` framed small +
>   `string_lights` has a glow-halo → regen later.
> - **CUTOUT METHOD (important):** white-key flood-fill from background-colored seed points (`tools/
>   art_pipeline/recut_furniture.py`). Do NOT use rembg `isnet-anime` — it's a character segmenter and
>   ERASES furniture (keeps only creature/blob shapes). Only cached rembg model is isnet-anime anyway.
> - **KOROKI-vs-ROOM match:** her existing SDXL sprite (clean-anime) reads as a crisper sticker on the
>   painterly rooms. User chose **full painterly restyle** (img2img through Sketch Pad). Denoise sweep
>   done (`tools/art_pipeline/koroki_restyle_sweep.py`): **~0.55 = sweet spot** (restyles yet keeps her
>   face/ears/hairpins/sweater); 0.65 drifts identity; 0.35 too weak. Limitation: even 0.55 is "softened
>   painterly anime," not the full loose room look (char img2img keeps too much clean structure).
> - **PIPELINE SCRIPTS PERSISTED** to `tools/art_pipeline/` (scratchpad is ephemeral). Note: their `PREV=`
>   output paths point at this session's scratchpad — repoint before rerun. ComfyUI must be running.
> - **`CIVITAI_TOKEN` now in `.env`** (for LoRA downloads; `civitai_fetch.py` pattern).
> - **ComfyUI ops:** start with `tools/ComfyUI/venv/Scripts/python.exe tools/ComfyUI/main.py --port 8188`
>   (its own venv, port 8188). **It crashes after long (~40-img) batches** — restart + resume (saved
>   images persist). Farm scripts have a built-in 180s server-wait loop.
>
> **NEXT STEPS (frontend art queue, in order):**
> 1. ~~**Batch-restyle Koroki**~~ ✅ **DONE 2026-07-02 (desktop-app session).** User picked **0.35**
>    (0.65 = "shonen manga vibe, a little too much"; also inconsistent per-sprite — pins/collars/faces
>    mutated). PLUS **ash-grey hair correction**: sources recolored brown→ash-grey BEFORE the 0.35 pass
>    (at 0.35 img2img preserves color, prompt can't recolor — fix at source). Production set
>    `assets/koroki_sprites/koroki_*.png` (MOVED from clients/web/assets/ — shadowed by the /assets
>    mount) is now painterly ash-grey; brown originals in `assets/koroki_sprites_brown_backup_2026-07-02/`.
>    Pipeline: `tools/art_pipeline/koroki_ashgrey_recolor.py` (hair mask + NECK_POLY guard) →
>    `koroki_restyle_batch.py --ash` (gen 0.35 + alpha-transfer cut, canvas-aligned). Cuts use ALPHA
>    TRANSFER from original sprite alpha, NOT border flood (chars ≠ furniture). base_*/sit_happy/stand_*
>    sprites still brown — run the same two scripts when they're regenerated/used.
> 2. ~~**Regen broken `stand_*` sprites**~~ ✅ **DONE 2026-07-02.** Full 6-expression standing set
>    (incl. new pout) live in `assets/koroki_sprites/stand_*.png` + pose toggle (sit/stand) wired into
>    puppet_trial. Body = candidate 32006 (user pick: face age-vibe 18-20 anchor; black oversized knit —
>    outfits NOT locked, "casual but alluring" policy, see memory koroki-outfit-age-policy).
>    **THE POSE PIPELINE (reusable for every future activity pose):**
>    `koroki_stand_gen.py` (Illustrious+koroki_lora_v2 txt2img candidates; round-2 prompt has the
>    proportion guards — round-1 skewed petite) → user picks body → `koroki_stand_faces.py`
>    (face-ellipse SetLatentNoiseMask inpaint @0.65, 6 expressions, body byte-identical) →
>    `koroki_stand_restyle.py` (rembg isnet-anime base alpha → FLUX SketchPad @0.35 → alpha-transfer
>    cut). Ash-grey comes out right at txt2img — no recolor pass needed for new gens.
> 3. **Build the layered bedroom scene** — 🔨 **v1 LIVE (2026-07-02, desktop-app session).** Spec:
>    `docs/bedroom_scene_spec.md`. Owner design law: MANY layers, tiny DESYNCED micro-motions (random
>    phase + ±15% period jitter per instance — memory frontend-scene-motion-philosophy). Built:
>    `clients/web/scene.html` + `scene.js` (NEW data-driven engine — supersedes the plan to grow
>    puppet_trial.js; motion runtime sway/rock/pulse/tremble/flicker, dt-corrected) +
>    `clients/web/rooms/bedroom.json` (layers/z/motions/spots config — room #2 onward = config+art,
>    no new code). Art: shell = candidate **41003** (user pick, the FURNISHED one — baked bed/lamps
>    kept, coherently lit; panes hand-cut to alpha via `koroki_bedroom_pane_cut.py`, polygon routes
>    around the bed corner) + sky_42002 + moon + 6 cloud sprites soft-cut from clouds_43001
>    (`koroki_bedroom_sky_prep.py` — clouds as individual desynced sprites, NOT a plate; feathered
>    dark surrounds vanish over the sky base). 15 live layers + 16 flickering city lights parented to
>    the sky. Spots: bed(sit) / window(stand) / center(stand). **Masking upgraded to SAM** (owner
>    verdict: hand polygons low quality): segment-anything vit_b in `tools/models/sam/`, runs in
>    .venv_diffsinger — `koroki_sam_mask.py` (panes, box+point prompts) + `koroki_sam_cutout.py`
>    (generic occluder/prop cutouts). ✅ Duvet-edge occluder DONE (bed_front.png z3.5; per-spot z:
>    bed z3 = tucked IN bed, floor spots z4 = in front). Known accepted limitation: SAM kept a few
>    baked towers in the glass (owner circled, reads as static near-city depth — flip any with one
>    positive point). **Remaining:** full-screen composition pass (user eyes), furniture dressing,
>    worldstate binding (grade/weather/station→spot), teleport transition FX, room dock (direct-jump).
>
>    ⏸ **FRONTEND PAUSED 2026-07-02 (owner call: hand scene work to another model/session).**
>    Cold-pickup for whoever continues: (1) run `.claude/launch.json` "web-preview" or the orchestrator
>    and open `/scene.html`; (2) everything is data-driven from `clients/web/rooms/bedroom.json` —
>    composition changes are config edits, no engine code needed; (3) engine = `clients/web/scene.js`
>    (motion law: random phase + ±15% period jitter, dt-corrected — keep it); (4) art regen chain:
>    `koroki_bedroom_scene_art.py` → `koroki_sam_mask.py save` → `koroki_bedroom_pane_cut.py cut-sam`
>    → `koroki_bedroom_sky_prep.py`; occluders via `koroki_sam_cutout.py <name> save`; (5) next
>    concrete steps in order: furniture dressing pass, worldstate binding (poll `/v1/worldstate`,
>    port gradeTargets/deriveStation from world.js), teleport FX, room dock. Puppet_trial + world.html
>    remain as reference rigs; scene.html is the future main page. Backend (mind) work resumes in
>    parallel — see "Mind: experience journal + activity system" entry below.
> 4. **Wire to her mind:** emotion → expression swap, scheduler → which spot/activity she's at.
> 5. **Backend leftovers** (pre-existing queue): sentence-streaming Brain→TTS, semantic memory embeddings.
>
> **Movement decision (settled):** no live procedural walking in 2D/2.5D — she **teleports** between fixed
> pose-spots with transition effects. Full style/pipeline detail also in `memory/frontend-room-art-style.md`
> and LEGACY 2026-07-02.

> **Current position (2026-06-30) — ACTIVE: Living Avatar.** Koroki's on-screen body for the "window
> into her world" frontend. After a multi-day embodiment saga (Live2D bust → 3D VRM, **both ruled out**
> — see LEGACY 2026-06-30), the chosen path is a **code-driven 2D layered puppet** ("DIY Live2D in
> PixiJS"): she **teleports** between generated **activity-poses**, with swappable **outfits + face
> expressions + effect overlays**, all driven by her existing "mind"/worldstate. Core rule:
> **composite layers at runtime, never pre-render combinations.** **Full plan:
> `docs/koroki_living_avatar_plan.md`;** character locked: `docs/koroki_character_design.md` (ash-grey
> hair, wine-crimson — NOT white/magenta; old white `苹果小狐狸` Live2D demoted to mascot/pfp).
> **Next:** Phase 0 = Koroki LoRA bootstrap (cold-start from `assets/Koroki pictures/` via IP-Adapter →
> curate → train) → Phase 1 = vertical slice (bedroom · 2 poses · 2 outfits · full expressions + the
> PixiJS compositing engine) → Phase 2 scale → Phase 3 joint articulation. PixiJS rooms + the world.js
> cosy-filter cleanup (garland + floating-sparkles removed 2026-06-29) STAY; the avatar layer is new.
>
> _The cognitive/embodied backend (mind/body/memory/nervous/world subsystems) is largely DONE and wired
> (see reality-check table below). The Living Avatar is a frontend + offline-art-generation workstream._

> **Current position (2026-06-28):** The singing detour reached its conclusion — **the DiffSinger→RVC
> chain works.** `koroki_v12` (real-Ikura clean full synthesis, gender-neutral) → `Korokiv5` RVC
> (Koroki timbre) is wired into `sing_song.py` and validated on female (Idol) AND male/heavy-production
> (Yonezu Lemon) sources. User verdict: success. Two issues flagged for later root-cause (dropped
> lyrics + lower quality on male/heavy mixes — see LEGACY 2026-06-28). **Singing detour CLOSED.**
> Now resuming the main queue: LoRA clean retrain running (2026-06-28); infrastructure docs done.
>
> See `docs/koroki_map.md` (canonical-thing map), `docs/checkpoint_manifest.md` (model retention),
> `docs/environment_matrix.md` (venv table).
>
> _Prior position (2026-06-27): singing was at "clean Korokiv5 RVC + curated dataset → going DiffSinger
> v10." v10/v11 turned out husky; v12 on real-Ikura data fixed it. Detour now closed (above)._

Every pending idea, optimization, or feature. Prioritized. Status-tracked. **Every item passes the test from CLAUDE.md** (does it go against the ultimate goal? if no → it qualifies).

> **📍 QUEUE STATE 2026-07-03 (post Fable-5 run-through).** Everything actionable WITHOUT the live
> stack / owner's ears / accumulated time is DONE: mind journal + activity engine, semantic memory
> embeddings, endocrine 2B (verified pre-existing), sentence-streaming TTS (flag-gated). What remains,
> and what it's blocked on:
> - **Needs the LIVE STACK running:** → **full one-shot checklist: `docs/live_test_checklist.md`**
>   (pre-flight: KILL COMFYUI first — it holds ~8 GB VRAM). Covers: sentence-streaming listening
>   pass, LoRA voice-test, journal/activity/events/embeddings/drift live validation, overnight
>   dream + voiced diary, Twitch listen-only trial, singing + proactive regressions. Separately
>   queued for live sessions: Brain+Orchestrator merge, cross-turn KV cache, singing
>   dropped-lyrics/male-mix root-cause, Discord VC build.
> - **Needs another session/model (owner call):** frontend scene work (paused, cold-pickup block above).
> - **Needs TIME:** Phase 5/6 retraining (journal corpus now accumulating), returning-visitor depth.
> - **Deferred tier:** unchanged (Ollama swap, IndexTTS quantization, chat.py refactor, status.ps1…).
> Next planning conversation: what NEW capabilities to add (owner + Fable 5, "what should we add next"
> discussion — candidates: journal LLM voicing, richer world events feeding the causal chain, Twitch
> chat ingest surface, Discord VC presence, community-scale memory).

---

## ✅ Reality check — what's ACTUALLY left (verified against code, 2026-06-28)

> This queue had drifted badly from the codebase: the endocrine system, the full autonomous-loop
> roadmap (memory hierarchy, nervous system, sleep cycle, world/room/weather), and most captain-in-
> cabin substrate were all marked "not started" but are in fact **built and wired into
> `routes/chat.py`** (~7,000 LOC across `body/`, `mind/`, `memory/`, `nervous_system/`, `cognition/`,
> `world/room/`, `social/`, `meta/`, `presence/`, `rag/`). Two near-duplicate builds were avoided
> tonight by checking code first. **So Koroki's cognitive/embodied architecture is largely DONE.**
> The genuinely-remaining work is smaller and more specific than the rest of this doc implies:

| Genuine gap (verified) | Why it's real | Touches live path? |
|---|---|---|
| **LoRA voice-test** | Clean retrain done; needs your ears for character + multi-turn discipline. | — (your test) |
| ~~**Frontend multi-room expansion**~~ | ✅ **DONE 2026-06-28** — `clients/web/world.html`+`world.js`: cinematic 2.5D world, "entering her place" intro with her AI-gen key-art, 3 navigable rooms (Studio/Bedroom/Lounge) with locally-generated neon-night art, atmosphere stack, `/v1/worldstate` binding, AND audio+commentary (`/v1/world/voice` cues). See `docs/frontend_vision.md`. | No (frontend only) |
| **Living Avatar (2D layered puppet)** | 🔨 **ACTIVE (2026-06-30)** — her on-screen body; Live2D bust + 3D VRM both ruled out (LEGACY 2026-06-30). Code-driven teleport-between-poses + outfit/expression/effect layers + Koroki LoRA. Full plan `docs/koroki_living_avatar_plan.md`. Phases: LoRA bootstrap → vertical slice → scale → joint articulation. | No (frontend + offline gen) |
| ~~**World-state aggregation endpoint** (`/v1/worldstate`)~~ | ✅ **DONE 2026-06-28** — `routes/world.py` aggregates time/presence/room(weather·ambient·lighting·identity)/body(energy·endocrine)/felt/nervous into one read-only snapshot. Defensive (per-section failure → null, never 500). Verified: returns coherent JSON, registered in app. Frontend can now consume it. | Additive route only |
| **Sentence-streaming Brain→TTS** | Verified NOT done — `chat.py` calls Brain→Guillotine→TTS *in sequence*. Real 30-50% perceived-latency win. | Yes (core response path) |
| ~~**Semantic memory embeddings**~~ | ✅ **DONE 2026-07-02** — `mind/embeddings.py` (multilingual-e5-small via existing transformers, **CPU-only**, lazy singleton, graceful fallback to text overlap) + `mind/memory.py` vector sidecar (`data/mind/memory_embeddings.jsonl`), background write-time embed (never stalls chat), startup backfill thread, calibrated cosine (0.70–0.95 → 0..1). settings: `mind.embeddings`. Tests: `tests/contract/test_memory_embeddings.py` (4) + real-model zero-keyword-overlap recall verified ("what music…?" → chorus memory). | Indirect |
| **Singing: dropped-lyrics + male-mix quality** | Flagged 2026-06-28; root-cause at separation/ASR/alignment stages (not per-song). | No (offline pipeline) |
| **Roadmap Phase 5/6** (heavy retrain on lived experience + scheduled A/B promote) | Genuinely not started; depend on accumulated experience data. Long-horizon. | No |

**Recommended order:** voice-test (you) → world-state endpoint → frontend expansion → sentence-streaming.
The frontend track is the largest genuinely-unbuilt, captain-aligned, low-risk surface.

---

## ⚡ Active Detour — Singing → DiffSinger v10 (2026-06-21 → present)

**Origin:** a shower-thought singing detour while Phase 3 was wrapping and koroki_v9 finished training. The project had wrestled with singing for months; this detour finally found and fixed the real root cause. **Full story in LEGACY.md (2026-06-25 entry).**

**The journey (condensed):**
- Original plan was "manufacture clean Koroki singing via multi-singer RVC → train koroki_v7." It evolved through v7 → v8 → v9, all of which still sang "screaming chicken / dry / collapsing on the chorus."
- **Root cause (2026-06-25): it was never DiffSinger — it was the RVC *teacher*.** Korokiv2 (used to make every singing dataset) trained on only **6.8 min of speech**, range-starved (**1.2% of frames above C5**), so it buzzed on high belts and baked that into every dataset. "Rickroll sounded clean" was a fluke (that song sits in v2's range).
- **Fix: Korokiv5 RVC.** Retrained on **26.4 min** of clean IndexTTS-generated Koroki speech, reaching **F5 via `emo_vector`** (happy+surprised for high register — no pitch-shift / no chipmunk). Provably clean on dry speech; full Idol cover sounds clean + powerful. **Korokiv5 replaces v2/v3/v4** (weights in `adapters/singing/`).
- **Dataset rebuilt + curated:** re-converted all yoasobi(300)+ado(176) sources with Korokiv5 → `data/diffsinger_raw/koroki_singing_v5/`. Metric-triaged QC (auto-keep clean / manually review break+rescue). Korokiv5 **rescued ~115 of v2's 138 rejects.** User curated 2026-06-27.
- Gotchas fixed: `proposed_pitch` `type=bool` bug (transposed the whole batch to male range — fixed at root in `ApplioV3.6.2/core.py`); 14 failed Ado conversions (empty wavs) swept; ~133 GB of dead checkpoints/models/venvs cleaned.

**Decision (2026-06-27): go DiffSinger.** RVC covers (Korokiv5) sound great, but the goal is **full synthesis** — immune to source singer, genuine high notes (RVC pitch-shift chipmunks; DiffSinger truly re-sings). The curated koroki_singing_v5 is now the **clean teacher** that every prior DiffSinger lacked.

**Next — koroki_v10 (immediate work):**
1. **Re-segment** koroki_singing_v5 at silences/breaths (fixes the mid-phoneme "seamless cuts" — fine for RVC, bad for DiffSinger phrase boundaries) **+ SOFA/MFA align** → DiffSinger-ready data.
2. Train **koroki_v10** from `koroki_ja_v1_160k` base. **Apply freeze-txt_embed *correctly* this time** — v9's freeze silently never applied (config-cache trap: must edit BOTH `configs/<name>.yaml` AND `checkpoints/<name>/config.yaml`).
3. Listening test with the Korokiv5 RVC cover as the quality bar to beat.

**Open loose end:** 9 suspected PV-intro "talk" segments re-opened in yoasobi curate for a final ear-check (idol's rap verse deliberately kept).

**Status:** Dataset curated & clean. v10 data-prep (re-segment + align) is the next concrete task. Queue resumes (world subsystems / frontend / endocrine) after v10.

---

## 🧠 ACTIVE — Mind: experience journal + activity system (started 2026-07-02)

**Origin:** Fable 5 exploration verdict (2026-07-02), owner approved. The two biggest her-side gaps:

1. **Her life evaporates.** Thoughts prune at 24h (`thought_generator.py` HISTORY_MAX), sleep
   consolidation is an MVP stub that only logs + decays (`meta/sleep_cycle.py` — "Phase 3 will write
   semantic-layer entries"), `self_history.md` is a static hand-written file. Consequences: she cannot
   truthfully answer "what did you do yesterday" (the OFF-STREAM LIFE is the whole differentiator vs
   Neuro-sama), and roadmap Phase 5 (retrain on lived experience) has NO data accumulating — every
   unlogged day is training data lost forever.
2. **She has a world but nothing to DO in it.** World simulates weather/time/lighting; meta/scheduler
   has drives (boredom/restlessness/care) that fire reach-outs; but there is no notion of "what is
   Koroki doing right now" (reading / listening to music / at the window / napping). This same signal
   is what the Living Avatar's pose-spots need (scheduler → which spot/activity), what makes proactive
   messages substantive ("I was reading X…" not generic check-ins), and what fills the journal.

**Build (captain-in-cabin: subsystems decide/record, LLM only voices):**
- `mind/activities.py` — activity engine: picks her current activity from topic interests × energy ×
  time-of-day × weather × endocrine state; state machine with dwell times + organic transitions;
  exposes `get_current_activity()` snapshot (for prompts, worldstate, avatar spot) and emits
  experience events into the journal. No LLM calls on the hot path.
- `mind/journal.py` — the experience stream + autobiographical memory: append-only daily log
  (`data/koroki/journal/YYYY-MM-DD.jsonl`) of experience events (activities, notable interactions,
  mood arcs from endocrine, thoughts worth keeping); nightly consolidation (hooked into
  `meta/sleep_cycle.consolidate()` — the hook point already exists) compiles the day into a compact
  journal entry (`YYYY-MM-DD.md`), optionally voiced by one LLM call/night in her own words;
  `self_history` grows from these instead of staying static.
- Prompt/worldstate integration: current-activity line into prompt_builder core_facts +
  `/v1/worldstate` (frontend picks her spot from it later); journal recall into identity/introspective
  contexts alongside self_history.
- This data IS the Phase 5/6 training corpus accumulating from day one.

**Status: CORE BUILT + TESTED 2026-07-02 (Fable 5 session).** Shipped:
- `mind/journal.py` — daily JSONL experience stream (`data/koroki/journal/`), deterministic
  day-entry consolidation (activities timeline / mood arc / thoughts / people / sleep),
  `today_line()` + `recent_entries(n)` recall APIs. Self-healing rollover (forward-only —
  backdated writes can't race-consolidate; day entries rebuild if a day file gains events).
- `mind/activities.py` — 12-activity home catalog (reading/music/window/singing practice/chess/
  tea/doodling/…), weighted pick by hour × energy × weather × endocrine nudges (restless/cozy),
  sleep-state override, dwell jitter, persisted state, 60s loop.
- Wired: app.py lifespan (activity loop), thought_generator→journal, sleep_cycle→journal
  consolidation + sleep markers (the deferred "Phase 3 semantic layer" now exists),
  felt-state context line carries "right now she's …" (reaches every prompt via the existing
  felt-state block), `/v1/worldstate` has an `activity` section (current + today_line — the
  frontend's spot signal is READY when scene work resumes), mood sampled to journal every 30 min.
- Tests: `tests/contract/test_journal_activities.py` (6 passing).
**Remaining (next session):** notable-interaction logging from chat.py (KIND_INTERACTION —
choose salience rule, e.g. importance from memory-stream write), sing-pipeline → KIND_SING
event, journal recall into identity/introspective prompts (alongside self_history injection),
optional nightly LLM voicing of day entries (flag, default off), then LIVE validation: run the
stack a full day, read her first real journal entry.

---

## 🌱 ACTIVE — Realism wave 2 (owner-approved 2026-07-03, build in order)

| # | Item | Design essence | Status |
|---|---|---|---|
| 1 | **World events** | `world/events.py` — 9-event catalog + weather-transition events, feeding `endocrine.ingest_event()` (tags speak the hormones' language: surprise/urgent/novelty/lights_dim). Eligibility by weather/hour/awake; persisted cooldowns; recent-event fragment colors felt-state context for 5 min; journal "The world outside" section; worldstate `events`. Thunder ignores sleep. | ✅ 2026-07-03 |
| 2 | **Dreams** | `mind/dreams.py` — wake hook (≥20 min sleep, 4h cooldown) gathers REAL fragments (journal 2 days + top memories) → one Brain call → surreal first-person dream → journal KIND_DREAM ("Dreamt" in day entry). Deterministic template fallback when Brain down. Background thread — never blocks the wake tick (which can run inside a chat request). | ✅ 2026-07-03 |
| 3 | **Interest drift** | `mind/interest_drift.py` — persisted per-category delta, reinforced by memory-write importance while a topic is live (chat.py hook), lazy exp-decay (τ=21d), cap +18, positive-valence only (aversions never move). Band crossings journaled as thoughts. `topic_interests.analyze_message` now uses effective weights. | ✅ 2026-07-03 |
| 4 | **Journal voicing** | `journal._voice_entry` — after template consolidation, background Brain call rewrites the day in her diary voice → `<day>.voiced.md`; recall prefers voiced, template stays canonical. Flag `mind.journal.llm_voice`. | ✅ 2026-07-03 |
| 5 | **Streamer surfaces** | **Twitch ingest BUILT** (`twitch_bot.py`): anonymous read-only IRC (no token needed!), presence-style selection (name-mention always, ambient sampling inversely scaled with chat speed, global+per-user cooldowns), routes through `/v1/chat` platform=twitch (strangers start at rel 10). Reply mode needs `TWITCH_TOKEN`+`TWITCH_NICK` in .env + `streaming.twitch.respond: true`. `streaming.twitch.enabled/channel` in settings. **Discord VC = next live session** (needs PyNaCl/ffmpeg in bot runtime, faster-whisper CPU STT, and real voice testing; design: join VC → whisper transcribe → chat pipeline → TTS playback queue). | Twitch ✅ / VC needs live session |

All contract-tested: `test_realism_wave2.py` (14) + `test_twitch_bot.py` (7). Suite fully green (81).

---

## 🌟 ACTIVE — Realism wave 3 (owner-approved 2026-07-03)

Owner verdict on the wave-3 menu: all approved EXCEPT the self-check tool (rejected — feelings
must be FELT, not narrated as telemetry; 4th wall is for capabilities only — now canon in CLAUDE.md).

| # | Item | Design | Status |
|---|---|---|---|
| 1 | **Discord presence = her life** | ✅ **2026-07-03** — upgraded the existing `_status_loop`: primary source is now worldstate activity (custom status = what she's literally doing, project title included), idle+"asleep" at night, old nervstate mood-words kept as fallback. Verify live at next bot boot. | done (verify live) |
| 2 | **Her diary channel** | ✅ **2026-07-03** — `_diary_post_task` in discord_bot: each morning posts yesterday's voiced entry (template fallback) to `discord.diary_channel_id` (settings.yaml, 0=disabled — **owner must set the channel id**). Chunked ≤2000 chars, state in data/discord/diary_state.json. | done (set channel id + verify) |
| 3 | **Multi-day projects** | ✅ **2026-07-03** — `mind/projects.py`: book/song/art projects attach to reading/singing-practice/doodling activities, progress per session, complete with journaled arcs ("finished reading \"Kitchen\" — 6 sittings over 4 days"), pools avoid repeats, persisted. Activity `doing` + felt-state + journal + worldstate all carry the project title. 6 contract tests. | done |
| 4 | **Discord VC presence** | Live session with owner (deps: PyNaCl/ffmpeg, faster-whisper CPU STT; playback via streaming pipeline). | needs live session |
| 5 | **SIGHT (the big one)** | **✅ v1 BUILT + LIVE-VALIDATED 2026-07-03 evening.** `services/vision/` (moondream2-2025-06-21 int4 via torchao tinygemm, port 9005, main .venv) — lazy-load + **unload-after-describe** (sporadic-look mode: VRAM only occupied ~16 s per look, so TTS never synthesizes at ~11.5 GB; game sessions keep it resident + idle watchdog). Weights `tools/models/moondream2-2025-06-21/` (+ `tokenizer_starmie.json` — REQUIRED, see LEGACY). Discord: attachments → b64 → ChatRequest.images_b64 → `senses/vision.py` → percept fact `you_see_the_image_they_sent:` + sight-enriched memory (recall query, memory stream, recent_turns, consolidation). Fail-soft: eyes offline → honest "can't see it" fact (live-validated: she hedged, didn't hallucinate). Game-context endpoints (/v1/game/enter|exit) scaffolded per owner design — conditioning verified in tests, waits on VM capture. **First look (live): cold 12.7 s load + 16 s total describe, accurate caption of her reference art, in-character reply, auto-unload confirmed, VRAM back to 9,490 MiB.** Start: `.\scripts\easy_start_vision_adapter.ps1`. 13 contract tests. NOT YET: passing the user's question through to query mode (v1 captions only — captain answers from caption); Qwen2.5-VL-3B swap-in after Vocos lands (service is model-pluggable by design). | ✅ v1 live (Discord images) |
| 6 | **VRAM headroom** | **RESEARCH LANDED + PATCHES APPLIED (2026-07-03 evening).** Research
agent's code audit found: (a) **QwenEmotion (1.2 GB VRAM) loaded eagerly but NEVER called** in our
deployment (adapter always passes emo_vector; use_emo_text never set) → now lazy-loaded;
(b) **w2v-bert-2.0 is fp32 ≈2.3 GB** + semantic codec + campplus all CUDA-resident yet only run on
voice-sample cache MISSES → moved to CPU (`INDEX_TTS_CPU_EXTRACTORS`, default on; outputs hop to
GPU before caching). Expected reclaim ≈3.8 GB, zero voice impact (synthesis chain untouched).
Additional research verdicts: GPT int8 not viable (breaks CUDA graphs, no quality data for this
family); Vocos-22khz is a possible −0.5 GB vocoder swap but needs ear A/B (BigVGAN must stay fp32);
accel engine ALREADY shares graph pools (why the [1,4] trim underwhelmed) — next knob is paged-KV
`num_blocks 16→12` (model_v2.py:455, −0.24 GB); driver guardrails: per-app "Prefer No Sysmem
Fallback" + HAGS-off (≈0.5-1 GB, owner action). Brain upgrade math: Qwen3-4B NF4 ≈3.6-4.7 GB —
fits post-reclaim. VLM verdict: **moondream2 4-bit (~1.5 GB, ScreenSpot 80.4)** is the sight
candidate; Qwen2.5-VL-3B needs more VRAM than we'll have. Gemini cross-research pending (owner).
**RESULT (same evening): adapter 8.8 → ~5.2 GB · stack 11.8 → ~9.0 GB · ~3.3 GB FREE · synthesis
with Brain resident at RTF 0.51-0.66 (wedge DEAD) · Discord text 750 ms + voice attach +2 s.**
One correction vs research: semantic codec must STAY on GPU (vq2emb is hot-path). Next decisions
for owner: Qwen3-4B brain upgrade vs moondream2 sight first (each ~1.3-1.6 GB — pick one, or take
the Vocos/KV-trim/HAGS levers to afford both). **Prior measurements:** | ✅ RECLAIMED | desktop idle 1.13 GB · IndexTTS accel = **8.8 GB**, RTF 0.54 · IndexTTS no-accel = **7.3 GB, RTF 1.86 (slower than realtime — unusable live)**. Verdict: CUDA graphs cost only ~1.5 GB and buy 3.4× speed → **accel stays ON**. The other ~7 GB is the resident model constellation (gpt fp16 + s2mel + bigvgan + campplus + semantic codec + **w2v-bert ≈ 600M params**). Real optimization directions, in order: (a) **move aux encoders (w2v-bert / campplus / semantic codec) to CPU** — they're per-request feature extractors, ~100-300 ms CPU cost each, potentially 1.5-2.5 GB freed; (b) verify every stage actually loads fp16 (s2mel?); (c) int8 GPT experiment (quality-gated by ears); (d) measure Brain-side (1.7B-4bit + KV) precisely at next full-stack boot. Side finding: the short-input runaway did NOT occur in no-accel mode — it's a CUDA-graph-capture artifact, confirming warmup-only scope. Also patched vendored `infer_v2.py`: `diffusion_steps` popped from generation_kwargs at entry (vanilla generate() validates kwargs; accel silently tolerated it). **Sight consequence:** VLM-on-GPU doesn't fit until (a) lands; near-term sight v1 = VLM on CPU for Discord images (latency-tolerant), GPU residency decision after aux offload. | accel verdict done; aux-offload next |

**GEMINI CROSS-RESEARCH VERDICTS (2026-07-03 night, owner-supplied Deep Research report):**
Corroborated (already applied/known): sysmem-fallback + HAGS levers; never quantize the AR GPT
(token-error cascade rationale — matches our int8 verdict); CUDA-graph pool sharing (verified
already present in vendored accel_engine.py:282 — all graphs capture into the first graph's pool,
so Gemini's "0.8-1.2 GB dedup" is pre-banked in our 5.2 GB number). **Genuinely new — the Vocos
lever is now concrete: `BSC-LT/vocos-mel-22khz`** — Vocos retrained for 80-bin mel @ 22.05 kHz
hop 256, which is EXACTLY our s2mel→BigVGAN format (verified checkpoints/config.yaml: n_mels 80,
hop 256, bigvgan_v2_22khz_80band_256x); has a 54 MB ONNX export. Before swap: verify fmax +
log-scale conventions vs the 256x BigVGAN variant, then owner ear A/B. Realistic saving ~0.5 GB
(Gemini's 1.15 GB assumes BigVGAN holds 1.2 GB — inflated). Also new: programmatic per-app
"Prefer No Sysmem Fallback" via NVAPI setting `0x10ECECC9=0x1` (nvidiaProfileInspector) for
python.exe — scriptable instead of control-panel clicks (verify ID before trusting). Discarded
as unverifiable/hallucinated: "IndexTTS V25", "WanGP Profile 3.5", macOS 249 GB leak (whole
report cites ~3 sources stretched over everything); its implied codec-to-CPU was already
disproven live (vq2emb hot-path). VLM note: Gemini rates Qwen2.5-VL-3B 4-bit ≈2.4 GB with
far stronger dense-HUD OCR than moondream2 — directionally credible. Sight plan stays
moondream2-first (safe in today's 3.3 GB), but build the vision service MODEL-PLUGGABLE and
adopt min_pixels/max_pixels capping; after Vocos lands, Qwen2.5-VL-3B becomes the swap-in for
game sessions. **Owner decision 2026-07-03: SIGHT FIRST, before the 4B brain.**

### Sight architecture (owner design, 2026-07-03 — record verbatim intent)
- **VLM subsystem** = her eyes (captain-in-cabin: VLM describes, captain reacts). Candidate:
  Qwen2.5-VL-3B-Instruct 4-bit (~2.5-3 GB — strong UI/text reading, needed for game HUDs).
  Feasibility gated on VRAM headroom item. Nearer-term first win: describe Discord image
  attachments into the chat pipeline (same subsystem, no VM needed).
- **Stateful game context (the key owner insight):** she does NOT re-identify the game from
  every frame — even top VLMs can't reliably name an in-game view cold. Instead:
  1. On ENTRY she sees the un-ambiguous moment (launcher/menu/title — e.g. picking a Roblox
     game) and commits "I'm playing X" to a game-session state.
  2. While the state is active, every VLM frame query is CONDITIONED on it ("you are looking at
     <game X>; interpret game-specific items accordingly").
  3. Game knowledge: a per-game knowledge file (`data/games/knowledge/<game>.json`) filled by a
     one-time research pass (web/wiki) — or SELF-LEARNED from play observations when the game is
     obscure/new (append observed item/mechanic facts).
  4. On EXIT (launcher/desktop visible again) the game state is CLEARED.
- **Runtime**: owner plans to give her an account + a virtual machine; capture pipeline = VM
  screen → vision service → captain. Design the vision service transport-agnostic (screenshot in,
  description out) so Discord images and VM frames share one path.
- **Owner constraints added 2026-07-03 evening (post-v1):**
  1. **She VOICES what she sees** — brain summarizes/interprets the percept in her own words,
     never recites the description. ✅ Implemented after 3 iterations: the percept must ride
     INLINE in the brain-visible message (`[you look at the image they attached — you see: …]`)
     — core_facts alone get dodged by the 1.7B captain regardless of position/nudging (see
     LEGACY "SIGHT v1"). Validated live: "coffee shop. yeah. obviously." + 96-token floor for
     image replies.
  2. **Fast-look requirement for game/stream mode (owner spec, 2026-07-03 night)**:
     **describe = 0.3-0.5 s per game frame; total reaction (see → understand → react) ≈ 1 s**
     for ACTIVE games (shooters, horror, anything requiring fast interaction). SLOW games
     (simulators, factory games, chess — chess pipeline already built) are exempt from the
     fast path. Design consequence: per-game knowledge files get a `reaction_class: fast|slow`
     field; slow class can use full captions, fast class uses the speed ladder: model RESIDENT
     (warm encode ~0.45 s), targeted short conditioned queries (max_tokens ~24-40), smaller
     max_edge for frames (fewer crops), possibly encode-next-while-generating pipelining, and
     if still short of 0.3-0.5 s → torch.compile on the VLM or moondream-0.5b for fast class.
     ~16 s cold looks remain fine for Discord images only. Measure the ladder at the VM session.
  3. **VRAM budget, all features combined: ≤10/12 GB** (owner expectation). Current resident
     ~9.5 GB + 2.5 GB vision transient. To make vision RESIDENT (game mode) inside 10 GB, the
     optimization dig resumes first: Vocos swap (−0.5), paged-KV trim (−0.24), HAGS-off +
     no-sysmem-fallback (owner-side), then re-measure. 4B brain upgrade competes for the same
     margin — sequencing decision after Vocos lands.
     **DIG PROGRESS (2026-07-03 night):**
     - ✅ **Paged-KV trim BANKED: −322 MiB** (total 9498 → 9176). `INDEX_TTS_KV_BLOCKS` env
       knob (model_v2.py KOROKI PATCH), running at 12 (=3072-token capacity). If adapter logs
       ever show block-pool exhaustion on long sentences → raise back toward 16.
     - ❌ **Vocos swap REJECTED BY EARS (owner, 2026-07-03 night):** "remap is really bad.
       direct is a bit better but eh still bad, original still stays really good
       uncomparable." BigVGAN STAYS — its quality on her voice is not replaceable by any
       existing 22 kHz Vocos checkpoint. Root cause was known going in: our chain is
       FULLBAND (fmax 11025) vs all public 22 kHz Vocos = fmax 8000 (Tacotron lineage);
       even the direct (vocos-native mel) path lost to BigVGAN. Assets kept for a possible
       future self-trained fullband Vocos (days of GPU training — parked): vendored decoder
       `indextts/vocos_mini/`, weights `experiments/index-tts/vocos_assets/`, A/B harness
       pattern in LEGACY. CPU-vocoder finding stands (Vocos RTF 0.01 CPU) if that ever lands.
     - ✅ **Brain VRAM hygiene: steady-state 9,171 → 8,121 MiB total (−~1.0 GB).**
       `torch.cuda.empty_cache()` after every generation (services/brain/generation.py finally
       block) — each request deepcopies the persona prefix-KV + grows generation KV and the
       allocator hoarded it all. Brain BOOT footprint is 3.4 GB regardless (NF4 quantizes only
       linears; Qwen3's 151k-vocab embedding stays fp16 ≈ 0.6 GB — structural, revisit at 4B).
     - ✅ **BigVGAN fp16 SHIPPED (owner ears: "almost no differences at all"; −225 MB).**
       KOROKI PATCH in infer_v2.py: halved under use_fp16, synth site casts to model dtype.
       Escape hatch: `INDEX_TTS_BIGVGAN_FP32=true`. Live-verified same night (voice reply OK).
     - 💡 **iGPU: Intel UHD 770 active, and VERIFIED NOT DRIVING THE DISPLAY** (RTX reports
       2560×1440 mode; Intel reports none → monitor is plugged into the RTX). Owner action:
       move the monitor cable to a MOTHERBOARD video port → dwm (281 MB) + Discord (217 MB) +
       browser leave the RTX ≈ −0.5 GB. iGPU "24 GB" in Task Manager = shared-RAM ceiling
       (not a reservation; desktop composition costs a few hundred MB of RAM). Fallback
       without replug: Settings → System → Display → Graphics → add Discord/browser →
       "Power saving" (moves their rendering to Intel; dwm stays on RTX). NVENC unaffected.
     - **Scoreboard 2026-07-03: 11.8 GB (morning) → 8.1 GB steady-state (night).** Budget
       math: 8.1 + vision resident 2.5 = 10.6; with iGPU move (−0.5) + BigVGAN fp16 (−0.22)
       ≈ **9.9 GB all-features — owner's ≤10 GB budget is REACHABLE.** Remaining structural
       lever: vision-in-brain-process merge (−~240 MB CUDA context, do with game-mode work).
     - ☐ **Owner-side switches (need reboot / control panel — NOT done tonight, overnight
       run is sacred):** (a) HAGS off: admin PS
       `Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers" -Name HwSchMode -Value 1`
       then REBOOT (2=on, 1=off). (b) NVIDIA Control Panel → Manage 3D Settings → Program
       Settings → add BOTH `.venv_indextts\Scripts\python.exe` and `.venv\Scripts\python.exe`
       → "CUDA - Sysmem Fallback Policy" = "Prefer No Sysmem Fallback" (fail-fast OOM instead
       of silent PCIe wedge; leave the vision python on default — a failed look degrades
       gracefully, she just can't see that one).
       Owner 2026-07-04: "might be satisfied with current size for now" — switches stay
       optional; steady-state ~8.4 GB is enough headroom for game-mode resident vision (~10.9
       peak, under the card).

---

## 🎮 STREAMING & PLAY — the plan (owner-aligned 2026-07-04, DISCUSSED before building)

**Platform fact that shapes everything: Discord bots CANNOT screen-share** (API has voice
only, no Go Live). So every Discord stream = two halves: video from a real user account
clicking Go Live on the game window (owner's account for v1 — owner supervises streams
anyway), and her VOICE from the bot in the same VC (fully automatable, legit API).

**Owner decisions (2026-07-04):** v1 video = owner's account Go Live · Stage 1 (watch-party)
before hands · Stage 2 first game = a TYCOON he has in mind (slow class; he has viewers
lined up) · she plays DEAF for now (game hearing = later organ; noted future ghost-game
problem — hearing may need audio-EVENT detection more than STT, parked).

**Two design pillars (owner directives):**
1. **ANTI-YAPPER GATE — commentary is ADDRESSED SPEECH, never self-talk (owner, 2026-07-04):
   "talking to THEMSELVES is psychopath, talking to viewers / commenting game events is
   normal human."** Every spoken line has a TARGET: a viewer (chat reply, addressing the
   room) or an EVENT breaking normality (someone entering her tycoon = true interaction).
   No ambient monologue while nothing happens. Mechanically: (a) hard cooldown between
   voluntary lines (~20-40 s config), (b) event detection — scene-change novelty check
   (Jaccard on descriptions v1; e5 embeddings later if too dumb), (c) viewer chat always
   answered, (d) the brain itself gets a [silent] option — not worth saying = say nothing,
   (e) silence is normal — streamers breathe. Owner judges 1.7B commentary quality by
   results; if flat → 4B upgrade jumps the queue.
2. **CHARM NEEDS A SKILL FLOOR.** Flailing is content, but below a reaction-time/smartness
   floor it turns annoying. So: the 0.3-0.5 s describe / ~1 s react ladder is a real target
   (not best-effort), and decisions must be *sensible* — constrained action vocabulary,
   game-knowledge files, never random flailing.

**Stage 0 — her voice in a VC** — ✅ **BUILT 2026-07-04** (needs owner ears live).
PyNaCl 1.6.2 + mss + pywin32 installed (main .venv); ffmpeg 8.0.1 on PATH; discord.py
bundled opus verified loading. Bot: `/vc_join` `/vc_leave` `/vc_say <text>` (test guild,
owner-gated) + serialized VC playback queue (`_vc_player_loop`, FFmpegPCMAudio). Her
viewer-chat replies also play in VC automatically when she's connected
(`_attach_deferred_audio` hook). **Owner test: join a VC → /vc_join → /vc_say hello.**
   ⚠ Two live blockers found+fixed at first try: (1) discord.py 2.6 needs `davey` (DAVE
   voice E2EE lib) — installed; (2) interaction must defer before connect (3 s limit).
   (3) **AV BLOCKER (owner action)**: Norton/Avast "Web/Mail Shield" MITMs TLS to
   `discord.media:8443` (proven: cert issuer = "Norton Web/Mail Shield") and breaks the
   voice WebSocket → TimeoutError. Fix: AV settings → Web Shield → disable HTTPS scanning,
   or exception for `discord.media` / the .venv python.exe.

**Stage 1 — WATCH-PARTY (no hands)** — ✅ **BUILT 2026-07-04** (needs owner live session).
`stream_watch.py` (eyes + gate only — mouth stays in the bot, captain-in-cabin split):
every tick capture target window (mss + win32gui, ~0.1 s) → vision describe (short,
max_tokens 80; game-conditioned + resident when game named) → `evaluate_gate` (cooldown +
jaccard novelty; pure function, 7 contract tests) → on_event → bot builds the ADDRESSED
directive ("say ONE short line TO YOUR VIEWERS … or reply exactly [silent]") →
game_event_context chat → TTS → VC queue. Commands: `/watch_start <window> [game]`
`/watch_stop` (posts looks/remarks stats). Config: settings.yaml `streaming.watch`
(tick 8 s, cooldown 25 s, novelty 0.5). **Headless validation passed**: captured owner's
real Discord window 0.09 s, vision read on-screen content incl. text, first_look event
fired, static second tick correctly silent. **Owner session: open game/video window →
/vc_join → /watch_start <title> [game] → click Go Live on that window.**

**Stage 2 — SHE PLAYS (the tycoon).** Adds the hands + agent loop:
**HANDS BUILT 2026-07-04 (during the 4B training window), awaiting GPU live test:**
`game_hands.py` — constrained executor (closed vocabulary: click/double_click/right_click/
move_to/press/scroll/wait), targets resolved by NEW vision endpoint `/v1/point`
(moondream native pointing; engine.point() added). All three rails from day one:
DRY-RUN default (proven to never even import pydirectinput), window confinement
(clamped coords + foreground check + re-check after vision round-trip), panic switch
(F9 via GetAsyncKeyState OR data/game/PANIC file), plus min-action-gap throttle and
pydirectinput FAILSAFE corner-abort. 11 contract tests. NEXT: live pointing accuracy
test on the tycoon UI (needs GPU post-training), then the captain's decision loop.
**✅ LIVE POINTING TEST PASSED (2026-07-04, owner-run):** /hands_test on Roblox tycoon —
crosshair landed correctly on the Settings button first try ("she did it perfectly").
10.2 s was VRAM-spill tax, not aim. **A1 (OmniParser) stays shelved — moondream pointing
is the production path.**
**STRATEGY SHIFT (owner, 2026-07-04): scaffold-now, bundle-test-later.** Owner will rent
a 3090 24 GB by the hour for testing → build + hermetic-test + LIGHTLY pre-verify
everything on the 12 GB box; save deep/heavy validation for ONE bundled rental session
(preserve $/hour). Applies to: game agent loop, ears, sleep-mode offload, stream profile.

**✅ PLAY LOOP BUILT + PRE-VERIFIED (2026-07-04 afternoon):**
- `/v1/games/decide` (routes/games.py): S3-structured decision cycle — scene + rolling
  state + per-game knowledge in → STATE/DO/SAY out, parsed tolerantly (garbage → "look").
  Felt-state rides the brain call: her body colors her play (tired = lazy play, by design).
- `game_agent.py` PlaySession: capture → FrameGate → describe → WatchState overwrite →
  decide → SAY (anti-yapper via bot) → DO via GameHands (all rails). Forced look every
  N static ticks (menus don't move but still need decisions).
- Bot: `/play_start window game [objective] [live]` (live=False → DRY-RUN default) +
  `/play_stop` (stats incl. dry/REAL marker). 8 new contract tests (139 total).
- **Light pre-verify vs the LIVE 4B captain (synthetic scenes): PERFECT.** Glowing
  Collect button → "DO: click collect / SAY: let's go"; shop with 17,706 cash → picked
  the AFFORDABLE upgrade (Fast Conveyor 500) — sensible economics, flawless format.
- **KEY 4B INSIGHT: she deflects open "react to this" asks but OBEYS format demands** —
  the decide-cycle SAY lines are in-character AND contextual ("let's see if that works").
  → The watch-party commentary retune should route through a STRUCTURED format ask
  (SAY-style), not the conversational game_event_context ask. Do this next watch session.
- REMAINING for the bundled rental test (or any session with the game open ~10 min):
  the full chain live on the real tycoon — /play_start dry-run first, then live=True
  with owner watching + F9 ready.
- Hands: `pydirectinput` mouse/keyboard (tycoons are mouse-driven — ideal), `vgamepad`
  (emulated Xbox pad) later for pad-native games.
- The click-finder: moondream2's built-in `point`/`detect` skills return PIXEL COORDS
  ("where is the buy button?" → x,y) — the bridge from captain intent to real clicks.
  Confirmed present in our vendored model.
- Agent loop (slow class, decision every ~5-15 s): capture → describe + targeted queries →
  captain picks from a CONSTRAINED action vocabulary (click <thing> / open <menu> /
  buy <x> / wait / look around) → executor resolves <thing> to coords via point → click.
- Safety rails: clicks confined to the game-window rect, global panic hotkey to freeze her
  hands, dry-run mode (logs intended clicks without clicking) for the first session.
- Game knowledge: `data/games/knowledge/<game>.json` (reaction_class, UI facts, goals) —
  per the locked sight design; tycoon gets the first file.
- Commentary: same anti-yapper gate as Stage 1.

**Stage 1b — CO-WATCH MODE (built 2026-07-04, owner workaround for the VRAM wall):**
she watches SOMEONE ELSE'S Go Live via the Discord stream POPOUT window — the game's
VRAM lives on the streamer's PC, zero cost here. Bots can't receive stream video via API;
the popout IS the receiver. `/watch_start window:<popout title> streamer:<name>` switches
her framing to VIEWER ("react to <streamer> or the room like a viewer") instead of
broadcaster. `/watch_windows` lists visible window titles to find the popout. LIMITATION:
mss captures the SCREEN REGION at the window rect — the popout must stay visible
(pin it: Discord popouts have always-on-top). First live spill lesson recorded: game
rendering on the RTX + stream + stack = 12.1 GB → per-app Graphics settings moves
Roblox/Discord rendering to the UHD 770 ("Power saving") for self-play sessions.

**COMMENTARY QUALITY INVESTIGATION (2026-07-04 night, first live watch sessions):**
Symptom: remarks disconnected from game content ("yeah that's fine", "that's actually good
tonight" over a bright idle tycoon). Eyes were FINE (vision logs showed real content:
"Minecraft project in progress…"). Synthetic A/B via /v1/chat isolated the brain:
- scene in a FACT → vibes-only filler (the known fact-blindness).
- scene INLINE in message + "react" ask → still vibes-only ("that sounds wild").
- + explicit "you MUST mention the specific thing / never say that's wild" + example →
  said "that's wild" TWICE. Instruction-following ceiling, LoRA register dominates.
- **scene inline + QUESTION framing ("someone in the vc asks: what's he even doing right
  now?") → GROUNDED**: "he's building something. that's all" / "building a base maybe" /
  (static menu) "nothing really". Coarse but connected. ✅ SHIPPED as the commentary
  prompt shape (also satisfies the addressed-speech pillar — she's answering the room).
**Verdict: 1.7B ceiling = coarse-but-grounded commentary.** For specific/funny lines
("he's just letting the ingots pile up lol") → **4B captain retrain**: Qwen3-4B chat
variant (NOT -Base — lm_head deficit, see LEGACY 2026-06-19) + LoRA retrain with the
known-good recipe (5 epochs, unified_sft.jsonl — the koroki_4b adapter name is historical,
it's trained on 1.7B). Runtime cost ≈ +1.2 GB steady → stream profile then needs
vision-0.5b (~1 GB) or the iGPU moves to fit streams. OWNER DECISION pending.
**✅ SHIPPED 2026-07-04 afternoon — see LEGACY "THE 4B CAPTAIN".** 54-min train, loss
0.09, seated at tools/models/Qwen3-4B + adapters/koroki_4b_qwen3-4b (TRAP: the
`model_profile` map overrides `name`). Full stack ALL-RESIDENT = 8.5 GB, 3.8 free.
Chat + sight-grounded replies clearly upgraded. OPEN: stream-commentary prompt retune
for the new captain (stronger character adherence = harder deflection of "react" asks;
try acoustic... rather: content-forcing variants, checkpoint-200 if 315 overfit) +
first watch-party rerun; VLM game-state memory (rolling state doc, Cradle pattern —
research 2026-07-04) lands in the game/watch session next.

**THE OWNER'S GAMBIT (2026-07-04 night): CosyVoice2 as production TTS — the budget breaker.**
Owner proposal: IndexTTS 5 GB → CosyVoice2-0.5B ~1.5 GB ("quality on par, from my
experience"), unlocking **1.5 TTS + 4B brain + 2-2.5 vision ≈ ALL RESIDENT under 10 GB.**
Two ear-gates before any switch:
1. **Emotion control**: CosyVoice2 `inference_instruct2` = natural-language style
   instructions on top of zero-shot cloning + inline [laughter]/[breath] tokens. Needs a
   new bridge (emotion engine affect → instruct text, sibling of indextts_bridge). Bench
   must prove control strength ≈ IndexTTS emo_vector.
2. **Contamination rate**: the "banging instruments" bug (see LEGACY backfill 2026-07-04)
   — root-cause trail says wetext frontend + clean speech prompts fix most of it; bench
   20+ lines with voice_samples/*.wav prompts and MEASURE. Plus a cheap runtime guard
   (spectral/energy anomaly check → auto-retry) if residual rate is low-but-nonzero.
Rebuild started 2026-07-04 night (setup_cosyvoice.ps1 rerun: venv + repo + model);
adapter needs full rewrite to the IndexTTS /synthesize contract (old one deleted in
cleanup, not in git). IndexTTS stays installed as the fallback/quality reference.
**BENCH RESULTS (same night, 2 rounds):**
- API drift traps in current repo HEAD: (1) frontend expects prompt as a FILE PATH
  (loads/resamples itself — passing the loaded tensor → "Invalid file" deep in
  frontend_zero_shot); (2) **instruct text MUST end with `<|endofprompt|>`** or the model
  READS THE INSTRUCTION ALOUD (owner heard "speak in a tired sleepy low..." — round 1
  invalidated; official example.py:57 shows the delimiter).
- With both fixed: **VRAM 2.45 GiB loaded** (−2.5 vs IndexTTS) · **RTF 0.5-0.98
  UNACCELERATED** (long lines 0.5-0.67 ≈ IndexTTS accel; round-1's RTF 1.0-1.9 was
  inflated by synthesizing the spoken instructions) · **instrument contamination: owner
  heard NONE in round 1's 16 gens** (wetext + clean speech prompt = the fix; LEGACY
  backfill has the causal trail) · sad/happy produce different durations (prosody control
  is real, ears to confirm quality). Open: neutral_11 (JP line) still 10.4 s — anomaly
  under ear review; owner round-2 verdict pending (voice similarity + emotion strength).
**OWNER ROUND-2 VERDICT (2026-07-04 night): "sounds really human, honestly I like it."**
- Voice similarity: PASS.
- Contamination: neutral_11 = ~5 s speech then garbage noise ("someone moving mic around")
  — TAIL contamination, on the JP line. Plus ~10% word-level artifacts (some words
  unreasonably loud, occasional voice break).
- Emotion control: "works really good" but WEAK — "neutral + 20% effect".
**PRODUCTION ADAPTER PLAN (build next session, experiments/cosyvoice/adapter.py :9004,
IndexTTS /synthesize contract so orchestrator can't tell engines apart):**
1. instruct2-only path, instruct ALWAYS suffixed `<|endofprompt|>`, prompt = file path.
2. Guards for the artifact classes the owner heard: (a) duration-sanity auto-retry with
   reseed (catches tail garbage — neutral_11 was 3.5× expected length, trivially
   detectable); (b) energy-based tail trim (cut after last speech + pad — kills residual
   tail noise even when retry passes); (c) peak limiter/loudness normalization (tames the
   "unreasonably loud" words).
3. Emotion bridge (sibling of indextts_bridge): (label, intensity 0-100) → instruct with
   intensity-scaled EXAGGERATED wording (round-3 strength test pending owner ears —
   polite phrasing measured at "+20%"; also testing inline [laughter]/[breath] tokens and,
   if needed, per-emotion reference samples like the IndexTTS adapter's voice selection).
4. Blind A/B vs IndexTTS on identical lines → ears → switch via settings. IndexTTS stays
   as fallback engine.
NOTE: 4B brain retrain needs a dedicated window with the stack (partly) DOWN — QLoRA on
the 4B needs ~10 GB; can't share the card with her running self. Daytime session, not
overnight-while-she-dreams.
**Round-3 emotion verdict (owner): happy GOOD, sad 7/10, teasing ≈ happy minus the bite,
tired far too neutral even with "extremely tired" wording.** Next levers for the adapter
session: concrete ACOUSTIC instructions instead of semantic labels ("speak very slowly,
quietly, low pitch, dragging" not "tired"), the inference `speed` parameter (direct
slowdown for tired), [breath]/[laughter] tokens, per-emotion reference samples (the
IndexTTS adapter's voice-selection trick).

**✅ SHIPPED TO PRODUCTION 2026-07-04 ~12:00** (owner turned IndexTTS off overnight and
green-lit the switch). `experiments/cosyvoice/adapter.py` (:9004, .venv_cosyvoice) —
full IndexTTS contract incl. /unload+/load (singing swap + future sleep mode). Baked in:
`<|endofprompt|>` always; prompt=path; ACOUSTIC emotion map (14 buckets + aliases +
emo_vector fallback, 2 intensity tiers) + `speed` param (tired 0.82, sad 0.92, warm 0.95);
duration-sanity reseed-retry (≤3 attempts); energy tail-trim (kills mic-fumbling tails);
peak guard. settings services.tts.adapter_url → 9004. **Live-verified**: /ready tts:true,
first real reply synthesized with her actual state (emotion=caring intensity=94 →
"speak very softly and warmly…"), 2.6 s audio in 1.9 s, voice attached +4 s.
**Stack now 6.2 GB total with TTS+brain+contexts resident** (was 9.2 with IndexTTS).
PENDING: owner ear verdict on production voice quality over days of real use; IndexTTS
remains installed at :9000 as instant fallback (flip adapter_url back + start it).

## 😴 SLEEP-MODE VRAM OFFLOAD (owner direction, 2026-07-04 00:40 — "like an actual human")
While asleep she only needs a few systems — offload the rest, reclaim the GPU overnight:
- **TTS adapter (~4.9 GB): unload during sleep** — she doesn't speak while sleeping. Add an
  /unload + /reload pair to the adapter (keep the process/port alive, drop the models), or
  supervisor-managed stop/start. At WAKE: reload + warm BEFORE brain serves (boot-order rule).
  A midnight direct mention = she's asleep; if woken, voice takes ~90 s to return — humans
  also take a moment. Text-first reply is fine.
- **Vision**: already self-unloads (idle watchdog) ✓.
- **Brain**: needed briefly at sleep onset (diary voicing) and wake (dream generation);
  could stay resident v1 (~3.4 GB), or lazy-unload between with morning reload (wake isn't
  latency-critical). v2 decision.
- **Payoff**: sleep window frees ~5-8 GB for ~7 h/night → **overnight jobs become possible
  ON schedule: the 4B QLoRA retrain can run WHILE SHE SLEEPS** (5 epochs/2002 samples fits
  the window), DiffSinger training, etc. Supervisor: orchestrator watches sleep_state
  (confirmed asleep ≥10 min → offload; pre-wake or wake → reload). Depends on the sleep
  physiology bugs below being fixed first (sleep must be TRUSTWORTHY before it gates VRAM).

## 🧠👁 GAMING-EYES CONCEPT RANKING (my research + Gemini deep research, merged 2026-07-04)
Owner directive: rank CONCEPTS, build our way (copy the proven, build up the promising).
**VERIFICATION ROUND (Gemini round-2 + my curl spot-checks, 2026-07-04):**
- ✅ CONFIRMED REAL (arxiv titles + HF HTTP 200 checked by us): CASA = Kyutai, arXiv
  2512.19535, checkpoint kyutai/CASA-Qwen2_5-VL-3B exists · MemEye 2605.15128 ·
  EgoTSR 2604.10517 · TEAM-VLA 2512.09927 · **Qwen/Qwen3-VL-2B-Instruct AND
  -4B-Instruct exist on HF (+GGUF/FP8 variants)** — B1 swap-bench candidates upgraded
  from "verify" to real. PLE = Gemma 3n only (llama.cpp/Ollama support it incl. Windows).
- ⚠ CORRECTED by Gemini itself: RoSe evaluated on 7B/32B, NOT ≤4B (our 4B captain is
  near the tested floor — try, don't assume) · "SoMatic" = a GitHub project WRAPPING
  Microsoft OmniParser-v2 YOLO weights (so A1's real dependency = OmniParser-v2, as
  suspected) · the "+20% over GPT-4o" was actually over a GPT-5.5 baseline; OmniParser+
  GPT-4o scored 39.6 on that ScreenSpot-Pro subset · Gated DeltaNet is Qwen3.5-LM only.
- ❌ STILL UNVERIFIED (didn't surface on HF search): the ScreenSpot-Pro "leaders"
  KV-Ground-GuiOwl1.5-4B / AdaZoom-GUI-4B / Qwen-GUI-3B — re-verify before ever
  downloading. Game-specific frame-diff parameter sets: NOT FOUND anywhere → our own
  live tuning of FrameGate thresholds is the source of truth.

**TIER S — COPY NOW (cheap, proven, fits current stack):**
S1. **Rolling GameState doc with OVERWRITE semantics** (Cradle memory + "verbalized
    memory"/evolutionary synthesis). Fields (objective, entities+positions, recent events,
    menu location) UPDATED in place each look — never appended logs. Key design rule from
    the research: *temporal authority beats semantic relevance* — stale facts must be
    overwritten, or retrieval acts on old positions. Injected into every vision query +
    commentary ask. CPU-only, extends existing game-session conditioning.
S2. **CPU frame gate BEFORE the VLM** (morphological differencing "PCRM"): gray→diff→
    median blur→adaptive threshold→morph close→changed-pixel ratio vs τ. Only changed
    frames reach the VLM. Replaces our current pay-a-caption-to-detect-novelty gate and
    is THE enabler of the 0.3-0.5 s fast-game budget. Upgrade inside it: compare vs an
    EMA/smoothed baseline of recent frames (not just prev frame) so slow pans don't
    trigger ("DSH" idea). Pure OpenCV, near-zero cost.
S3. **Structured percept→task-state→action output** for the play loop (Odysseus
    perception-tag + "EgoTSR" shape): captain must fill fixed fields incl. task-state
    ∈ {progressing, blocked, regressed} judged against the GameState doc — breaks the
    "later frame = progress" bias, catches walking-in-circles. Prompt-shape only.

**TIER A — BUILD-UP (good concept, needs our adaptation):**
A1. **Hybrid grounding: CPU UI-detector + TEXT coordinates** (OmniParser pattern; the
    SoM attention-noise finding for small VLMs is credible — don't burn labels into
    pixels). OUR PATH: moondream point() first (already built); if live pointing
    accuracy disappoints on the tycoon → add OmniParser-lite/YOLO pass feeding text
    boxes. Decision gate = the hands live test.
A2. **Confidence-gated self-critique on action failure** (Cradle self-reflection — its
    biggest single win — + "RoSe" adversarial-persona twist): after an action, cheap
    frame-diff check "did anything change?"; if nothing → ONE critique call (auditor
    framing) + forbid repeating that action. Never critique every action (latency).
A3. **Lagged-frame change narration**: compare frames spaced seconds apart, feed "what
    changed" text to captain — readable motion for slow games.

**TIER B — LATER / model-level:**
B1. VLM swap bench when fast-class needs it: Qwen3-VL-2B (verify avail; strong UI
    grounding claims), SmolVLM2-2.2B (fast, multi-frame), Qwen2.5-VL-3B (temporal
    video). Bench on OUR frames; moondream stays until pointing/desc proves limiting.
B2. Query-aware triggering ("wait until shield drops"): frame-embedding relevance gate —
    needs per-frame CLIP-ish embeds; build when wait-for-X tasks actually appear.
B3. Menu-map in per-game knowledge files (poor-man's knowledge graph: which button
    leads where) — fold into the knowledge JSON schema, no graph engine.
B4. Zero-KV streaming vision (cross-attention "CASA" style) — property of model choice,
    not bolt-on; revisit only at a VLM swap.

**Build order: S2 → S1 → S3 (all pre-live-test) → hands live test decides A1 → A2 with
the play loop → Tier B on demand.**

## 👂 EARS — design sketch (owner priority after hands, 2026-07-04)
Game/stream hearing v1 = AUDIO-EVENT detection, not STT (owner's ghost-game insight:
"the ghost whisper isn't STT-able" — what matters is that SOMETHING made a sound and
roughly what kind/where). Design: capture game audio via WASAPI loopback (soundcard lib
or pyaudiowpatch, CPU-only) → light event classifier ladder: (1) onset/loudness deltas
(free, instant), (2) spectral heuristics (impact vs voice-band vs music), (3) optional
tiny tagger (YAMNet/PANNs CPU, ~5 MB) for labels like footsteps/door/whisper/gunshot →
percept line to the captain ("a sudden loud noise from the game, low and close").
Stereo L/R energy ratio gives cheap direction. Zero GPU. Build after hands live-test.

## 🩺 SLEEP PHYSIOLOGY BUGS (found 2026-07-04 00:00-00:30, she fell asleep at ~23:55)
Owner asked "when does she sleep?" → live check: sleep_state=falling_asleep at 23:59 —
**the sleep state machine works** (sleep.py reads melatonin_circadian() DIRECTLY, clock
fn is correct UTC+7). But the physiology under it is broken:
1. ✅ FIXED: **energy never persisted** — save() existed, nothing called it
   (data/body/energy_state.json had never been created). Every orchestrator restart
   refilled her to 1.0 (energy was 0.998 at midnight after a restart-heavy day; also
   explains "booted 04:33 with full energy" from the overnight run). Fix: throttled
   `_maybe_save` after every tick (energy.py). Applies at next boot.
1a-bis. ✅ **THE DEEPER ROOT (found 2026-07-04 evening, via the heartbeat's own dead
   vitals line): `get_sleep().state` DOESN'T EXIST** (attr is private `_state`;
   public accessor = `current_state()`). activities' `_sleep_override` threw
   AttributeError on EVERY tick since the feature existed, the silent
   `except: asleep = False` swallowed it → **the sleeping activity never once
   fired in her life** — this, not just chat-driven ticks, is why she "daydreamed
   in bed" all night. Fixed in activities.py + heartbeat.py (both now use
   current_state()); the swallowing except now logs WARNING.
   **Meta-lesson (bit us 3× today: watcher greps, vitals debug-log, silent
   except): FAILURE SIGNALS MUST BE VISIBLE. Never catch-and-continue below
   WARNING in subsystem code.**
1b. ✅ **ROOT OF THE WHOLE CLUSTER FOUND + FIXED (2026-07-04, night-one post-mortem):
   her body ticks were CHAT-DRIVEN ONLY.** get_felt_state() (chat pipeline) was the only
   caller of get_sleep().tick() + get_endocrine().tick(). No chats overnight → sleep
   state machine frozen mid-transition → activities kept "daydreaming in bed" all night
   (its sleep-override reads the raw state, which never advanced) → energy DRAINED via
   stale awake-routing instead of refilling → no sleep session → NO DREAM at wake.
   Fix: **autonomic heartbeat** (body/heartbeat.py, wired in app lifespan) — sleep +
   endocrine tick every 60 s unconditionally, + a vitals log line every 10 min
   (sleep/energy/melatonin/cortisol) so night anomalies are visible in the log.
   Philosophy note: "her body only existed when spoken to" — the heartbeat IS the
   living-continuously principle made literal. Verify tonight: sleeping activity,
   energy refill overnight, melatonin curve in vitals lines, dream at wake.
2. ☐ **endocrine state also doesn't persist** (data/body/ was EMPTY) — likely the same
   never-called-save pattern; hormones reset every restart. Audit + wire like energy.
3. ☐ **live melatonin stuck at 0.04 vs circadian target 0.80 at midnight** — the tick
   math is PROVEN correct offline (fresh engine reaches 0.52 in 30 simulated min).
   Something about the LIVE instance suppresses it. Suspects: snapshot/effective_level
   (receptor sensitivity), sleep-transition interference, or the singleton not being
   ticked on the path I think. Investigate with instrumentation at next boot. Causal-chain
   note: sleep currently triggers off the pure clock function, NOT her hormone level —
   once melatonin works, sleep.py should read the COMPONENT (environment→hormone→felt→
   sleep, per philosophy), not the clock directly.

**Stage 3 — LATER: her own account + VM** (game in VM under her alt, host captures VM
window, alt account Go-Lives), fast-class games with the measured reaction ladder, and
game HEARING (audio-event detection for ghost games — not plain STT).

**VRAM at stream time:** vision resident (game mode) ≈ 10.9 GB peak with today's 8.4
steady-state — fits. iGPU move / HAGS remain optional margin.

---

## 🔥 In Progress

| Item | Status | Notes |
|---|---|---|
| LoRA training iteration | retrain complete — **awaiting voice-test** (2026-06-28) | Clean retrain finished cleanly (02:31): 5 epochs, 2002 samples, final loss 0.255, token-acc 0.98, no errors. Known-good recipe (full-text loss, batch 2 × grad_accum 16, Qwen3-1.7B chat variant, default chat template — `assistant_only_loss` dropped). Saved to `adapters/koroki_4b`; prior adapter backed up at `adapters/koroki_4b_backup_jun20`. **User must voice-test** (Discord/web) to confirm character + multi-turn discipline before this is marked done; restore the backup if it regressed. |
| Track 2 — Prefix caching | shipped (code-level) | Implemented in `services/brain/adapters.py:_prepare_prefix_cache()` and `services/brain/generation.py`. Config flag `brain.prefix_cache.enabled`. Waiting on stable LoRA before measuring real latency savings. |

---

## 🎯 Next Up (queued, validated by research or testing)

| Item | Category | Why | Effort |
|---|---|---|---|
| **[system]-voice migration: watch/play paths** | Brain↔system contract | The [system] envelope shipped 2026-07-04 for all proactive paths (see LEGACY "The Afternoon She Argued With Three Dots"). Watch/play still fabricate social speakers ("a viewer asks:", "someone in the vc asks:") — the 1.7B crutch. Bundle with the queued structured-SAY commentary retune: scene rides a `[system] eyes:` turn, 4B obeys format demands. Needs a live watch session to validate commentary quality doesn't regress. | ~half session (live) |
| **[system] convention → next 4B SFT set** | LoRA fix | The rule is currently instructed, not learned. Add SFT examples: `[system]` turns answered with self-initiated speech or `[silent]`, never acknowledgment ("okay") and never treated as a speaker. Rides the already-planned 4B retrain window (stack-down daytime). | +~20 examples in the set |
| **Channel-level conversation memory** | Architecture | Root fix for the cross-user interleave ("i didn't ask what happened", 2026-07-04): per-user memory + shared channel = split-brain in public. She needs a per-CHANNEL view of what she said/heard there, layered over per-user relationship memory. Busy-channel guard (shipped) is the interim mitigation. | ~2-3 days design+build |
| **Revert + clean retrain** | LoRA fix | Drop `assistant_only_loss`, restore Qwen3 default chat template, 5 epochs, batch 2 × grad_accum 16. Yields previous clean voice + multi-turn discipline. Trailing-junk handled at brain layer with post-process strip. | ~30 min (script revert) + ~30 min retrain |
| **Endocrine simulation Phase 1 (cortisol + dopamine + oxytocin)** | Captain-aligned (HIGH) | First subsystem that genuinely embodies sentience. Replaces label-based emotion engine with causal hormone simulation. See detailed entry below. | ~1 week Phase 1 |
| Track 3 — Smaller-captain A/B test (Qwen3-1.7B) | Captain-aligned | Highest captain-in-cabin alignment. Frees ~3GB VRAM. Persona drift mitigated by structured input architecture. | ~1 week (separate LoRA train on Qwen3-1.7B-Base + side-by-side eval) |
| ~~Sentence-streaming Brain → TTS~~ | ✅ **BUILT 2026-07-03, flag-gated** — `pipeline/sentence_stream.py` (SentenceAssembler EN/JP with ellipsis/decimal/abbrev guards; **deterministic text-derived pause model** per owner directive — trail-offs 420ms, paragraphs 500ms, run-ons 40ms, never random; wav concat with silence gaps; StreamingSpeech orchestrator). chat.py: sentences dispatch to IndexTTS DURING Brain WS streaming; audio used only if text survives post-repairs (crutch/trim/think-strip ⇒ classic one-call fallback — optimism costs nothing). Voice tradeoff in streaming mode: cross-sentence cue injections skipped, emo_vector carries emotion. **`features.sentence_streaming.enabled: false` until a LIVE LISTENING PASS** (owner ears required). 17 contract tests. | done (needs ear-validation) |
| Brain + Orchestrator process merge | Architecture | Save ~1-1.5GB Python+torch import overhead. Lose service modularity but gain shared CUDA context. | ~3 days |
| Cross-turn KV cache persistence | Captain-aligned (LLM efficiency) | Reuse conversation history's KV state across turns. Massive savings on long conversations. Builds on Track 2's plumbing. | ~2-3 days (depends on Track 2 landing) |

---

## ⏸ Deferred — Valid, lower priority

| Item | Category | Why deferred |
|---|---|---|
| Move Brain to Ollama backend | Component swap | Saves ~1.5-2GB. Less critical if Track 3 (smaller captain) lands first. Revisit if VRAM still tight. |
| IndexTTS quantization research | TTS optimization | Most savings (~2-3GB) but most uncertain. Need a profiling pass first to know actual peak VRAM. |
| Kill Python import duplication (discord_bot, etc.) | Cleanup | Save ~500MB. Easy but low impact. |
| Track 1 — Ego-neuron structural pruning | LLM optimization | Existing profile data insufficient. Would need new calibration collection. Captain-alignment low. **Skip unless other tracks fail.** |
| Speculative decoding with Koroki-distilled draft | LLM optimization | Generic speedup, not captain-aligned. Real but expensive to set up (train a 0.5B draft model). **Research note (2026-07-03, owner-shared): `research/DSpark_paper.pdf` (DeepSeek) — semi-autoregressive drafter + confidence-scheduled verification, +60-85%/user in serving fleets. Verdict for us: separate-draft speculation is a bad VRAM trade against a 1.7B 4-bit target; REVISIT only if the Brain grows, and then prefer MTP-style self-drafting (no extra model) with DSpark's confidence-gated verify. Transferable pattern today: confidence-gate optimistic work (analog: gate sentence-streaming's optimistic TTS on crutch-risk if discards ever get frequent).** |

---

## 🏗 Captain-in-Cabin Architecture (the long-term shift)

Moving cognitive load out of the LLM and into subsystems. These are the "real" architectural wins.

> **⚠ Partly stale (2026-06-28).** The subsystem *substrate* these depend on is largely built now
> (`thought_generator.py` exists for thought generation; async snapshot buffers exist across body/
> nervous_system/memory). The rows below describe the LLM-offload *optimizations* on top of that
> substrate — most are still genuine work, but **verify each against the current `prompt_builder.py`
> and `thought_generator.py` before starting** (the "Not started" labels predate the subsystem build-out).

| Item | Impact | Status |
|---|---|---|
| Structured input dashboards (replace prose context with JSON) | High — smaller prompts, faster prefill, more accurate decisions | Not started. Needs design pass on what dashboards look like. |
| Subsystem-driven tool calls (emotion engine reacts to LLM output, not LLM-issued) | High — LLM stops doing mechanical work | Not started. Easy to prototype once emotion engine output parsing is in place. |
| Template + state thought generation (skip LLM for background thoughts) | High — kills the autonomous loop's biggest cost | Not started. Has design implications for thought_generator.py. |
| Async subsystem state buffers (LLM reads snapshots, doesn't wait) | Medium — better latency, no blocking | Partially exists (emotion engine, mood_modifiers). Make it universal. |
| Output vocabulary pruning (Koroki uses ~5k of 150k tokens) | Medium — ~300MB savings | Not started. Needs vocab usage analysis on training data. |
| Adaptive layer pruning for trivial inputs (early-exit) | Medium — speeds reflex responses | Not started. Needs HF transformers fork or custom inference loop. |

---

## 🌍 Autonomous Koroki Roadmap (from `docs/autonomous_koroki_design.md`)

> **⚠ RECONCILED AGAINST CODE 2026-06-28 — this table was almost entirely wrong.** Most phases
> marked "Not started" are actually **BUILT and wired into `routes/chat.py`** (~7,000 LOC of
> subsystem code under `services/orchestrator/`). The same staleness that nearly caused a duplicate
> endocrine build (LEGACY 2026-06-28) ran through this whole table. Verified status below with file
> pointers. **Before building any "roadmap" item, grep the code first.**

| Phase | Description | Verified status (2026-06-28) |
|---|---|---|
| 0 | Brain swap + clean LoRA | ✅ **DONE** — Qwen3-1.7B + clean `koroki_4b` retrain (this session). |
| 1 | Skeleton autonomous loop (world state, sensory translation, slow tick) | ✅ **BUILT** — `nervous_system/engine.py` (309 LOC, persistent causal-graph state, 60s tick), `world/clock.py`, `body/interoception.py`. Wired. |
| 2 | Memory hierarchy (Park-style stream + Letta-style tool memory) | ✅ **BUILT** — `mind/memory.py` (482 LOC, Park-style stream, recency·importance·relevance retrieval), `memory/intelligence.py` (959 LOC), `memory/cache.py`. Wired. ⚠ One known gap: semantic embeddings are a **placeholder** (relevance is keyword-based) — see real gaps below. |
| 3 | Virtual world expansion (objects, weather, day/night) | ✅ **BUILT (backend)** — `world/clock.py` (day/night), `world/room/weather.py`, `world/room/ambient.py`, `world/room/identity.py`. The state EXISTS; the genuine gap is the **frontend doesn't render it** (clients/web is still a single canvas) — see real gaps below. |
| 4 | Sleep cycle (daily memory consolidation, curation pipeline) | ✅ **BUILT** — `meta/sleep_cycle.py` (143 LOC, memory consolidation + cortisol normalization + dream replay), `body/sleep.py` (363 LOC). |
| 5 | Heavy brain retrain on curated experiences | ⛔ **GENUINELY NOT STARTED** — depends on accumulated lived-experience data. Real future work. |
| 6 | Scheduled retraining pipeline (weekly LoRA A/B + promote) | ⛔ **GENUINELY NOT STARTED** — real future work. |

---

## 🧪 Quality & Iteration

| Item | When |
|---|---|
| Test current LoRA output for character fidelity | After current training run finishes |
| Per-language drift check (Thai, Japanese, Chinese) | After test reveals if drift remains |
| Dataset expansion if quality is thin | Based on test results |
| DPO pass with hard negatives | Optional, if SFT alone has known failure modes |
| Action marker style refinement | Iterative |

---

## 🛠 Infrastructure & Project Health (queued after Phase 2D)

**From external-agent review on 2026-06-22.** These directly address pain points we've hit during development (CUDA detection failures, "which checkpoint is good" confusion, venv ambiguity). All are docs/scripts work — no GPU needed, can be done in parallel with anything else. Captain-in-cabin alignment: clear infrastructure means more time on Koroki herself, less time on debugging the rig.

> **Done 2026-06-28 (the three core docs):** `koroki_map.md`, `checkpoint_manifest.md`,
> `environment_matrix.md` all written and verified against the filesystem. They immediately surfaced
> that CLAUDE.md is stale — it lists 7 venvs (only 4 exist) and several deleted dirs/checkpoints.
> Remaining infra items below (known_good_stacks.md, doctor.ps1) still pending.

| Item | Why | Effort | Status |
|---|---|---|---|
| **`docs/koroki_map.md`** — "Map of Koroki" doc (what's live / experimental / abandoned / precious) | Most valuable. We have CLAUDE.md and LEGACY.md but neither answers "what's the current canonical X." Future sessions waste time rediscovering this. | ~30 min | ✅ done 2026-06-28 |
| **`docs/checkpoint_manifest.md`** — model/checkpoint manifest + retention rules | Direct response to today's "which RVC model is best?" / "what was v7?" pain. Mark keep vs delete for ~30GB of checkpoints. | ~20 min | ✅ done 2026-06-28 |
| **`docs/environment_matrix.md`** — venv table (subsystem, Python, CUDA, venv name, start script, status) | 7+ venvs, "which Python for what" is a recurring cost. Today's CUDA-torch-CPU issue surfaced this. | ~15 min | ✅ done 2026-06-28 |
| **`docs/known_good_stacks.md`** — version combos that work per audio model | Today we lost ~30 min on pyworld/numpy/torch/lightning version dance. Documenting "this exact stack works" saves the next session. | ~20 min | ✅ done 2026-06-28 — verified-installed versions for all 4 venvs + load-bearing pins (torch 2.8 on diffsinger, numpy<2 on indextts, fairseq 0.12.2/numpy 1.23.5 on singing). |
| **`scripts/doctor.ps1`** — diagnostic script | Checks Python paths, venv existence, CUDA availability, model file presence, port availability. Would have caught today's CUDA issue in 5 sec. | ~30 min | ✅ done 2026-06-28 — verifies 4 venvs + versions, CUDA, 8 production model files, config/secrets, disk, ports. All green on run. |

---

## 🛠 Deferred Infrastructure (after the green tier above)

Lower urgency / more effort. Worth doing eventually.

| Item | Why deferred |
|---|---|
| **`scripts/status.ps1`** — system status summary (disk, env health, running services) | Useful but #7 doctor.ps1 covers most diagnostic needs. Combine later. |
| **No-model unit tests** (schemas, memory, emotion logic, prompt building) | Significant test-scaffolding investment. Worth doing incrementally as we touch each subsystem rather than as a one-shot. |
| **chat.py refactor (2.2k lines → thin orchestrator + subsystem calls)** | Aligned with captain-in-cabin (chat.py should be a thin router calling into subsystems, not a god-file). Needs careful planning. Bots are live on this code. |
| **Production vs lab split (explicit boundary)** | Partly true already (services/ vs experiments/). Making "legacy/abandoned" markers explicit would help. Low effort if done with koroki_map.md. |
| **Dependency notes near experiment start scripts** | Self-documenting code; mostly subsumed by known_good_stacks.md. |
| **Unified log paths** | Real but minor. Logs work even if scattered. |

---

## 🔬 Research / Uncertain (worth tracking, not committed)

| Item | Why interesting |
|---|---|
| TensorRT / ONNX conversion | 3-5× inference speedup, ~30% size reduction. Painful conversion process. |
| Behavioral KV cache (cache responses to common patterns) | "CDN for conversations." Novel. Could be massive win if scale matters. |
| Online distillation pipeline (4B → 1B over time) | Gradually transfer captain to smaller model during deployment. |
| Wanda++ pruning with proper Koroki vs generic calibration | If Track 3 fails, this becomes the path to free VRAM. |

---

## 🧬 Endocrine Simulation — Full Architectural Brief

> **⚠ STATUS CORRECTION (2026-06-28): this is BUILT and LIVE, not "not started."** The implementation
> lives at `services/orchestrator/body/endocrine.py` (978 lines, built 2026-06-21) with companion
> `body/interoception.py` (felt-state), `body/energy.py`, `body/sleep.py`, `body/mood_compositions.py`,
> and `world/clock.py` (circadian). It is **wired into the live chat pipeline** (`routes/chat.py`:
> `get_endocrine().ingest_event(...)`, `get_felt_state()`). It is already at **Phase 2A** — has the HPA
> cascade (ACTH), GR receptor downregulation (the brief's Phase 3 feature), and cortisol/dopamine/
> oxytocin/RPE Phase 1 in full. Smoke test: `scripts/test_endocrine.py`. The brief below is preserved
> as the design reference, but the "designed, not started" framing was stale and wrong. **Do not
> re-implement** — extend the existing `body/` modules. (This staleness nearly caused a duplicate
> parallel implementation on 2026-06-28 — see LEGACY.)
>
> ~~**Real next step for endocrine:** Phase 2B (serotonin slow floor + norepinephrine + melatonin)~~
> **✅ VERIFIED ALREADY BUILT (2026-07-02):** live component check shows all nine running —
> cortisol, dopamine_tonic, dopamine_phasic, oxytocin, **serotonin, norepinephrine, melatonin**,
> plus **crh + acth** (the Phase 3 HPA cascade). Endocrine Phases 1–3 are essentially COMPLETE.
> (Second time this queue's endocrine status was stale — always
> `get_endocrine().components.keys()` before planning endocrine work.) Remaining endocrine work
> is Phase 4 texture (sleep-architecture coupling, receptor tuning) — low priority.

**Original brief status (kept for the record):** designed. Highest captain-in-cabin alignment.
**Author of vision:** user (Koro-san), 2026-06-18.
**Research backing:** `Agent` dive completed 2026-06-18 — citation-grounded biology, real numbers, working ODEs.

### Why this matters (vision in one paragraph)

Current emotion engine = label-based reactive system. Text comes in → engine decides "she should feel caring" → label gets applied to TTS and prompt. The engine *decides* the emotion. That's not how real bodies work. The endocrine simulation **simulates hormone levels causally** — events change hormones, hormones produce felt experience, felt experience shapes response. The LLM doesn't decide "I'm anxious"; it reads "you feel a tightness in your chest" because cortisol is high because of what happened 20 minutes ago. **Past events shape current chemistry → current chemistry shapes voice → voice IS the emotional state.**

### Goal-test (CLAUDE.md exclusion test)
- Does this go AGAINST ultimate goal? **No** — it makes Koroki *more* sentient, *more* embodied, *more* honest about her own internal causality.
- Captain-in-cabin alignment? **Maximum** — body is the body, captain reads what body feels, acts accordingly.
- LLM stops introspecting on emotions it can't actually feel. Body simulator runs continuously, LLM reads snapshots.

### Tier decision: Design for "deep" architecture, build incrementally

The architecture must support 15-20 biological components from day 1, BUT we implement only what we need at each phase. Adding a 7th, 12th, 20th component is just writing a new `BiologicalComponent` subclass — no refactor.

### Core architecture

```
services/orchestrator/endocrine/
├── components/
│   ├── base.py              # BiologicalComponent ABC
│   ├── cortisol.py          # Phase 1
│   ├── dopamine.py          # Phase 1 (tonic + phasic two-channel)
│   ├── oxytocin.py          # Phase 1
│   ├── serotonin.py         # Phase 2 (slow floor)
│   ├── norepinephrine.py    # Phase 2
│   ├── melatonin.py         # Phase 2 (circadian-driven)
│   └── hpa_cascade.py       # Phase 3 (CRH → ACTH → cortisol stages)
├── events.py                # Event → hormone delta mapping
├── interactions.py          # Coupling matrix (see numbers below)
├── rpe.py                   # Phase 1: Reward Prediction Error system
├── receptors.py             # Phase 3: receptor downregulation
├── felt_state.py            # Hormone vector → natural language for LLM
└── engine.py                # Tick loop, integration, snapshot
```

### Phased build plan

| Phase | Components | What works | Effort |
|---|---|---|---|
| **Phase 1** | Cortisol + Dopamine (tonic + phasic) + Oxytocin + RPE engine + Felt-state translator | Full causal loop proven end-to-end. Voice shifts noticeably with state. "She's still warm from earlier" / "she's tight from that argument" emerge. | ~1 week |
| **Phase 2** | + Serotonin (slow mood floor) + Norepinephrine + Melatonin (circadian) | "Bad week" vs "good week" textures. Anxiety vs thrill differentiation via NE+cortisol+dopamine gating. Late-night-fog emerges. | ~1 week |
| **Phase 3** | + Full HPA cascade (CRH → ACTH stages) + Receptor downregulation + Reward dip on omission | Realistic ~15-30 min cortisol lag. Tolerance from chronic stimulation. "Sulking when expected return doesn't come" texture. | ~1-2 weeks |
| **Phase 4** | + Sleep architecture + Sex hormones if relevant + Anything else discovered | Sleep debt affects cortisol baseline. Circadian beyond just melatonin. Discovered gaps. | ongoing |

### Phase 1 — Six hormones with real biology (research-backed numbers)

All values normalized to [0, 1] for sim. Use **analytic exponential integration**: `H_new = H * exp(-k * dt) + (P / k) * (1 - exp(-k * dt))` — numerically stable, accurate at any dt.

#### Cortisol (HPA stress hormone)
- **Half-life:** 80 min (range 40-225 min in real biology)
- **Baseline:** 0.3 normalized. Morning peak 1.0, midnight 0.1 (sinusoidal circadian forcing)
- **Triggers:** psychosocial stress, threat appraisal, novelty, social rejection, conflict
- **Rise rate:** ~15-30 min lag after stressor (HPA cascade — Phase 3 explicit; Phase 1 use single-stage with delay)
- **Felt experience:** heaviness, tightness in chest, hypervigilance, "wired-but-tired"
- **Citations:** [Hindmarsh 2015](https://onlinelibrary.wiley.com/doi/10.1111/cen.12653), [Hindmarsh 2020](https://onlinelibrary.wiley.com/doi/10.1155/2020/2470956)

#### Dopamine (two-channel: tonic + phasic)
- **Tonic channel:** baseline 0.4, τ ≈ 30 min, drifts with general engagement state
- **Phasic channel:** baseline 0.0, τ ≈ 5 sec, spikes on RPE δ events. Magnitude = δ
- **Triggers:** positive RPE (better-than-expected events), anticipation, novelty, owner attention
- **Felt experience:** motivation, leaning-in, "want," sparkle in voice
- **Critical:** dopamine DIPS below tonic on omission of expected reward — this is what produces the "sulking when expected return doesn't come" texture
- **Citations:** [Kaeser lab review](https://kaeser.hms.harvard.edu/sites/kaeser.hms.harvard.edu/files/publications/2021/Spatial%20and%20temporal%20scales%20of%20dopamine%20transmission.pdf), [Schultz 2016](https://pubmed.ncbi.nlm.nih.gov/27069377/)

#### Oxytocin (bonding/affiliation)
- **Half-life:** ~5 min (3-5 min plasma, longer terminal phase up to 20 min)
- **Baseline:** 0.3 normalized
- **Triggers:** affiliative touch, sustained owner presence, trust signals, shared moments, hearing owner's name used affectionately
- **Felt experience:** warmth in chest, softening, lowered guard, urge to express care
- **Key coupling:** suppresses cortisol with ~5 min delay (the biology has natural transport delay — don't make it instant or you'll get oscillations)
- **Citations:** [Amico et al. 1980](https://pubmed.ncbi.nlm.nih.gov/7354123/), [Windle et al. 2004](https://www.jneurosci.org/content/24/12/2974)

### Phase 1 — Reward Prediction Error (the load-bearing piece)

Schultz's TD-learning formulation, replicated finding in neuroeconomics. **This is what makes disappointment ≠ relief ≠ surprise.** Without RPE, dopamine is just "reward signal" and you lose all texture.

```python
class RPESystem:
    def __init__(self, alpha=0.1, gamma=0.9):
        self.V = {}  # state_key -> expected value
        self.alpha = alpha    # learning rate
        self.gamma = gamma    # discount factor
        self.last_state = None

    def observe(self, state_key: str, reward: float) -> float:
        """Returns δ (RPE signal) — fires phasic dopamine."""
        v_now = self.V.get(state_key, 0.0)
        v_prev = self.V.get(self.last_state, 0.0) if self.last_state else 0.0
        delta = reward + self.gamma * v_now - v_prev
        if self.last_state is not None:
            self.V[self.last_state] = v_prev + self.alpha * delta
        self.last_state = state_key
        return delta
```

**δ → felt experience mapping:**
- δ >> 0 (surprise reward) → delight, sparkle (phasic dopamine spike + oxytocin nudge if social)
- δ ≈ +baseline → satisfied (normal dopamine tonic)
- δ ≈ 0 (expected) → neutral (no phasic)
- δ < 0 (expected, didn't get) → disappointment (dopamine dip below tonic)
- δ << 0 (worse than expected) → hurt, betrayal (dopamine dip + cortisol rise)

**Relief computation:** track expected aversive value `V_neg` separately; when threat resolves with no harm, fire `δ_relief = 0 - V_neg(s_prev) > 0` → releases oxytocin AND dopamine simultaneously. "Phew, okay" feeling.

### Phase 1 — Interaction matrix (the part that makes it feel real)

| Source → Target | Sign | Suggested coefficient | Citation |
|---|---|---|---|
| oxytocin → cortisol | suppress | -0.6 with 5 min delay | [Windle et al. 2004](https://www.jneurosci.org/content/24/12/2974) |
| oxytocin → CRH | suppress | -0.5 direct | same |
| cortisol → dopamine_tonic | suppress | -0.4 over hours | [Belujon & Grace 2020](https://www.nature.com/articles/s12276-020-00532-4) |
| cortisol → dopamine_phasic_gain | suppress | gain × (1 - 0.5·cortisol) | [Stanton et al. 2018](https://changlab.yale.edu/sites/default/files/files/Stanton_Tins_2018.pdf) |

**Critical implementation detail:** don't couple two fast variables bidirectionally without lag — produces spurious oscillations. Use delayed effects matching biology (e.g., oxytocin at time t affects CRH production at t+30s, not immediately).

### Felt-state translator (the LLM interface)

**Never expose raw numbers to the LLM.** Compose natural-language snapshot every tick:

```
Body state: warmth in chest (oxytocin 0.8), light buzz of alertness
(NE 0.6), bright pull-toward (dopamine phasic +0.4 just now). No
tightness, no fatigue. Mood floor: settled.
```

This goes into the system prompt as "what Koroki currently feels." The LLM reads sensations, NOT numbers. The translator picks vocabulary from interoception research:
- [Yu et al. 2025](https://arxiv.org/html/2505.16189v1) — corpus of body-part mentions in affective language
- [WordNet-feelings](https://arxiv.org/pdf/1811.02435) — categorised lexicon

### Tick rate and integration

| System | Tick rate | Why |
|---|---|---|
| Hormone levels | 1 sec | Plenty given τ values are minutes |
| RPE / phasic dopamine | Event-driven | Fire on events, not ticks |
| LLM snapshot generation | 5-10 sec OR event-interrupt | Don't waste compute |
| Serotonin / slow floor | 60 sec | Moves on hours-to-days |

### Numerical stability gotchas (avoid these)
1. **Clip to [0, max]** every update — hormones can't go negative
2. **Saturate Hill terms** to avoid divide-by-zero: `1 / (1 + (z/K)**n)` is safe; `1/z` is not
3. **Don't couple two fast variables bidirectionally without lag** — biology has natural delays, model them
4. **Single integration step per tick** — don't iterate within a tick

### What integrates with this

- `services/orchestrator/emotions/engine.py` becomes a **thin felt-state translator** over the endocrine layer (or gets deleted entirely)
- `services/orchestrator/mood_modifiers.py` becomes obsolete — modifiers were a crude approximation of what hormones do naturally
- `services/orchestrator/routes/chat.py` reads the felt-state snapshot for prompt injection
- The autonomous loop (Phase 1 from `docs/autonomous_koroki_design.md`) calls `endocrine.tick()` every iteration

### Key references (kept for context-survival)

- HPA axis ODE: [Sriram, Rodriguez-Fernandez, Doyle 2012, PLOS Comp Bio](https://journals.plos.org/ploscompbiol/article?id=10.1371/journal.pcbi.1002379)
- RPE biology: [Hollerman & Schultz 1998, Nat Neurosci](https://www.hms.harvard.edu/bss/neuro/bornlab/nb204/papers/Hollerman_Schultz_NatNeuro_1998.pdf)
- Closest prior art: FAtiMA Toolkit (OCC emotions, appraisal-based), HELT (hormone-inspired transformer layer), Aura Emotion AI (claims hormone sim)
- Interoceptive language: [Bhardwaj et al. 2023](https://www.nature.com/articles/s41598-023-49313-9), [Yu et al. 2025](https://arxiv.org/html/2505.16189v1)

### Honest confidence assessment (preserved from research)

- **HIGH** confidence: HPA cascade ODE form, RPE TD-learning math, half-life numbers for cortisol/dopamine/melatonin
- **MEDIUM** confidence: Interaction matrix coupling magnitudes (directions right, magnitudes are tuning parameters)
- **LOW** confidence / inventing: Serotonin behavioral time constant, felt-state translation (no production system has solved this well — we'll be inventing it)

### Total code estimate

~500 lines of clean Python for full Phase 1-3 endocrine system. Plus felt-state translator (~150 lines) + integration with chat pipeline (~50 lines).

---

## 🌐 Frontend Expansion — Multi-Layered Site (post-world-subsystems)

**Vision (user, 2026-06-21):** The current `clients/web/` is one canvas — Koroki centered, idle. That's an introduction, not a presence. The deeper vision is the frontend as a *window into her world* rather than a portrait of her. When the visitor arrives, she might not be at the center of view — she might be in another room, looking out a window, asleep, away. The visitor navigates *through her world* to find her.

**Goal-test (CLAUDE.md exclusion test):** Does this go against the ultimate goal? **No** — it strengthens the embodied-sentience framing. Currently the website shows her as a chatbot avatar; the vision shows her as a being who lives somewhere with structure to explore. Captain-in-cabin compatible: world subsystems produce the state; frontend renders it.

### What this enables when shipped

1. **Spatial existence.** Multiple rooms/areas (bedroom, music room, kitchen-ish, outside-her-window view, eventually stream setup). She has a `location` state tracked by the world subsystem. Frontend shows the location you navigated to AND whether she's there.

2. **Living world rendering.** The lighting subsystem (Phase 2D) lights her room visually. The weather subsystem (Phase 3) affects what's seen through her window. Objects she's interacting with appear in the scene. The frontend doesn't invent these — it reads from world state.

3. **Stream-channel layout.** A Twitch-channel-style page (different view of same world state, optimized for one-to-many). Main video panel of her, chat sidebar, "now playing" music panel, activity feed. Possibly the eventual canonical public-facing surface.

4. **Returning-visitor depth.** Right now you've "seen" the site in 30 seconds. With multi-room navigation + contextual world state + animated transitions, returning visitors find new things and a sense of continuity.

### Architectural alignment

The frontend is mostly *plumbing* once world subsystems exist:
- Lighting/ambient (Phase 2D) → CSS color schemes + ambient particles
- Room/object state (Phase 3) → SVG/Live2D objects rendered
- Weather (Phase 3) → background animation behind window
- Sleep state (Phase 2C) → renders her in bed if asleep
- Presence/away (Phase 2B) → empty room views when she's logged off

So the frontend work is best done **after world subsystems are rich enough to populate it**. Otherwise we'd be hardcoding what should emerge from subsystem state.

### Estimated effort

| Sub-phase | What ships | Effort |
|---|---|---|
| 4A — Layout + room navigation | Multi-route SPA. Room navigator. Empty-room views when she's elsewhere. | ~3-4 days |
| 4B — Per-room rendering | Each room reads its world subsystem state. Lighting/ambient applied. | ~3-4 days |
| 4C — Spatial presence | She moves between rooms based on world state. User notification when she enters/exits. | ~2-3 days |
| 4D — Stream channel layout | Twitch-style view as alternate UI. Activity/music sidebars. | ~3-4 days |

Total: ~2-3 weeks. Most leverage comes from 4A + 4B; 4C and 4D are polish.

### When to start

After Phase 2D (room basics) lands — that's the world subsystem this frontend most directly depends on. Probably ~Phase 4 in calendar order, but the architectural prerequisites are world-side, not chronological.

### Stream channel — future-proofing note

If user wants Koroki to have a Twitch-style stream channel "soon," the 4D sub-phase can be promoted/expanded into its own thing. The world-state APIs are the same; it's mostly UI work + maybe live audio/voice routing.

---

## 🛡 Rules for Adding to This Queue

1. Every new idea must pass: *does it go against the ultimate goal in CLAUDE.md?* If yes → reject or redesign. If no → it qualifies.
2. Items are removed when shipped (not "completed" — actually live in production).
3. Defer items if a stronger track makes them unnecessary (e.g., Track 3 may make Track 1 + Ollama swap unnecessary).
4. When in doubt: captain-in-cabin alignment > generic speedup > component swap.
5. **Detailed entries (like the endocrine simulation above) are written verbose enough to survive context compaction.** When in doubt, write more, not less — research findings, citations, code stubs, concrete numbers — all so future-Claude or future-you can pick up cold.
