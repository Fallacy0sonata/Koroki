# Map of Koroki

> **Purpose:** the single answer to "what is the *current canonical* X." CLAUDE.md says what the
> rules are; LEGACY.md says why decisions were made; this file says **what is live right now, what
> is experimental, what is abandoned, and what is precious (irreplaceable).** When in doubt about
> which model/script/venv is the real one, this file wins.
>
> Last verified against the filesystem: **2026-06-28.** Re-verify after any cleanup.

---

## 🟢 LIVE — the production path (this is what actually runs Koroki)

| Concern | Canonical thing | Where | Notes |
|---|---|---|---|
| Entry point / routing | Orchestrator (:9882) | `services/orchestrator/` | Auth, emotion engine, streaming, singing route |
| LLM / "captain" | Qwen3-1.7B + `koroki_4b` LoRA | `services/brain/`, adapter `adapters/koroki_4b/` | 4-bit NF4. Pivoted 4B→1.7B (chat variant) for VRAM; adapter dir name is still `koroki_4b`. |
| Speech TTS | IndexTTS (:9000) | `experiments/index-tts/adapter.py` | Primary engine. Checkpoints present (`gpt.pth` etc.). QwenTTS is legacy fallback only. |
| Singing — voice model | **DiffSinger `koroki_v12` → RVC `Korokiv5`** (the CHAIN) | `experiments/diffsinger/sing_song.py` | v12 = clean real-Ikura full synthesis (gender-neutral); Korokiv5 = Koroki timbre. Default ckpt **40000**. |
| Singing — RVC weights | `Korokiv5_300e_34500s_best_epoch.pth` + `Korokiv5.index` | `adapters/singing/` | Replaces v2/v3/v4. The only RVC model to use. |
| Discord UI | `discord_bot.py` | repo root | Primary surface. 40KB monolith (tech debt). |
| Web UI | Live2D canvas | `clients/web/` | Secondary surface. |
| Config | `config/settings.yaml` | — | Single source of truth. Secrets in `.env`. |

**The singing chain (memorize this):** any song → download → separate stems → transcribe+align →
build .ds (phonemes from `experiments/diffsinger/phonemes_63.txt`) → extract variance curves from the
source vocal → **DiffSinger koroki_v12** (sings notes cleanly, any source gender) → **RVC Korokiv5**
(makes it Koroki) → mix with instrumental. Validated on female (Idol) and male/heavy-production
(Yonezu Lemon) sources, 2026-06-28.

---

## 🧠 SUBSYSTEMS — the captain-in-cabin "body" (built, under `services/orchestrator/`)

These are the continuously-running subsystems the LLM reads felt-state from. **Verified present
on disk 2026-06-28**; "wired" = confirmed imported by `routes/chat.py`. For the *design* rationale
of each, see `docs/koroki_subsystem_atlas.md`; for *priorities*, `docs/master_queue.md`. This table
is the **actual built layout** (which differs from the atlas's aspirational paths).

| Subsystem | Real path | Status |
|---|---|---|
| Endocrine (cortisol/dopamine/oxytocin + RPE, HPA cascade, receptor downreg) | `body/endocrine.py` (978 lines) | 🟢 **LIVE — wired into chat.py.** At Phase 2A. Smoke: `scripts/test_endocrine.py`. |
| Interoception / felt-state translator | `body/interoception.py` | 🟢 **LIVE — wired** (`get_felt_state()`). |
| Energy / fatigue | `body/energy.py` | Built. State in `data/body/`. |
| Sleep | `body/sleep.py`; cycle in `meta/sleep_cycle.py` | Built. |
| Mood compositions | `body/mood_compositions.py` | Built. |
| World clock / circadian | `world/clock.py` | Built (drives cortisol/melatonin circadian). |
| Social — presence / relationship / residue | `social/presence.py`, `relationship.py`, `residue.py` | Built (Phase 2B–3). State in `data/social/`. |
| Proactive scheduler | `meta/scheduler.py` | Built (Phase 3). State in `data/meta/`. |
| Emotion engine (legacy label-based) | `emotions/engine.py` | 🟡 Being superseded by the endocrine layer (brief says it becomes a thin translator or is deleted). Still present. |

> **Important:** the **endocrine system is NOT at `services/orchestrator/endocrine/`.** It is at
> `services/orchestrator/body/endocrine.py`. (A duplicate `endocrine/` package was started and removed
> on 2026-06-28 after discovering the real one — the master_queue had it mislabeled "not started.")
> Other subsystem dirs also exist and warrant their own verified audit: `autonomy/`, `mind/`,
> `cognition/`, `nervous_system/`, `memory/`, `rag/`, `presence/`, `games/`, `guards/`, `pipeline/`.

---

## 🟡 EXPERIMENTAL — exists, not in the production path

| Thing | Where | Status |
|---|---|---|
| DiffSinger `koroki_v10`, `koroki_v11` | `checkpoints/koroki_v10|v11/` | Superseded by v12 (husky — variance/RVC-artifact issues). Kept for comparison only; deletion candidates (~20GB). |
| Singing v1 — RVC/Applio adapter (:9001) | `experiments/singing/adapter.py` | yt-dlp→demucs→RVC→mix. Standalone cover pipeline, separate from the DiffSinger chain. |
| Singing transpose | `experiments/singing-transpose/sing_song.py` | Variant pipeline. Not the canonical sing_song.py (that one is `experiments/diffsinger/sing_song.py`). |
| Style-Bert-VITS2 | `experiments/style-bert-vits2/` | Only a training-data-gen script remains. Abandoned as a TTS engine. |
| SFT experiments | `experiments/sft/` | `koroki_sft_v1.jsonl` is live training data; rest is experimental. |
| `adapters/koroki_personality_v1` | `adapters/` | Old personality LoRA (pre-`koroki_4b`). Reference only. |

---

## 🔴 ABANDONED / REMOVED — do not look for these, they're gone

The **2026-06-27 disk cleanup** (~133 GB freed) deleted several things CLAUDE.md still mentions.
**CLAUDE.md is stale on these — trust this file:**

| Mentioned in CLAUDE.md | Reality |
|---|---|
| `.venv_singing_v2` | **Deleted.** Seed-VC singing v2 (persistent buzzing artifact). |
| `.venv_cosyvoice` | **Deleted.** CosyVoice adapter venv. |
| `.venv_fishspeech` | **Deleted.** Fish Speech venv. |
| `experiments/singing-v2/` (Seed-VC, :9002) | **Deleted.** Buzzing artifact, abandoned. |
| DiffSinger `koroki_v2`–`v9` checkpoints | **Deleted** in cleanup. Only `koroki_ja_v1_160k`, `v10`, `v11`, `v12` survive. |
| `data/diffsinger_raw/japanese/phonemes.txt` | **Deleted** — and it broke the build (the "0 segments" bug). Replaced by stable `experiments/diffsinger/phonemes_63.txt`. |
| Korokiv2/v3/v4 as singing models | Superseded by Korokiv5. The `.pth`/`.index` files still sit in `adapters/singing/` but must not be used (range-starved teacher — see LEGACY 2026-06-25). |

---

## 💎 PRECIOUS — irreplaceable, expensive to regenerate, BACK UP before risky ops

| Thing | Where | Why precious |
|---|---|---|
| `koroki_ja_v1_160k` base | `checkpoints/koroki_ja_v1_160k/` | 160k-step base. Every DiffSinger finetune starts here. Clean general singing mechanics. |
| `Korokiv5` RVC weights | `adapters/singing/Korokiv5_*.pth` + `.index` | The Applio training dir was deleted; these copied weights are the ONLY surviving copy. |
| `koroki_v12` ckpt 40000 (+ 74000) | `checkpoints/koroki_v12/` | The production singing model. ~6h to retrain. |
| `koroki_4b` LoRA | `adapters/koroki_4b/` | Current personality. Backup at `adapters/koroki_4b_backup_jun20/`. |
| Training corpora | `data/training/lora/unified_sft.jsonl`, `experiments/sft/koroki_sft_v1.jsonl` | Curated by hand over many sessions. |
| Real-Ikura singing corpus | `data/diffsinger_raw/ikura_real/` | 211 real wavs + transcriptions. The v12 teacher data. |
| Per-user memory | `data/memory/` | Koroki's continuity of self. |

---

## Quick "which one is real?" answers

- **Which sing_song.py?** → `experiments/diffsinger/sing_song.py` (NOT singing-transpose's).
- **Which RVC model?** → Korokiv5. Never v2/v3/v4.
- **Which DiffSinger exp?** → koroki_v12 @ 40000. Never v2–v11.
- **Which captain model?** → Qwen3-1.7B + `adapters/koroki_4b` (dir name is legacy; model is 1.7B).
- **Which phoneme dict for the build?** → `experiments/diffsinger/phonemes_63.txt` (stable copy; do NOT depend on anything under `data/diffsinger_raw/<dataset>/`).
- **Which TTS?** → IndexTTS (:9000). QwenTTS is fallback only.
- **How many venvs are there really?** → Four: `.venv`, `.venv_indextts`, `.venv_singing`, `.venv_diffsinger`. (See `environment_matrix.md`.)
