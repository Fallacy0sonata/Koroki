# Checkpoint & Model Manifest

> **Why this exists:** direct answer to the recurring "which checkpoint is good?" / "what was v7?" /
> "can I delete this 10GB?" pain. Lists every model/checkpoint, marks **KEEP vs DELETE**, and gives
> retention rules so disk doesn't fill (we hit 100% once — see LEGACY).
>
> Last verified against the filesystem: **2026-06-28.** Sizes are approximate.

---

## DiffSinger acoustic checkpoints — `experiments/diffsinger/DiffSinger/checkpoints/`

| Name | Steps on disk | Size | Verdict | Notes |
|---|---|---|---|---|
| `koroki_ja_v1_160k` | 160000 | 848M | 💎 **KEEP — precious** | Base for every finetune. Clean general singing mechanics. |
| `koroki_v12` | 10k–74k (9 ckpts) | 10G | 🟢 **KEEP — production** | The singing model. Real-Ikura data, gender-neutral. **Use ckpt 40000.** Thin to {40000, 74000} → reclaims ~8GB. |
| `koroki_v11` | 10k–74k | 10G | 🔴 **DELETE candidate** | Superseded by v12 (husky). Keep only until v12 fully confirmed; then delete (~10GB). |
| `koroki_v10` | 10k–74k | 10G | 🔴 **DELETE candidate** | Superseded by v12. Delete (~10GB). |
| `pc_nsf_hifigan_44.1k_hop512_128bin_2025.02` | — | 55M | 🟢 **KEEP** | NSF-HiFiGAN vocoder. Required by all DiffSinger inference. |
| `audio_separator_models` | — | 610M | 🟢 **KEEP** | BS-RoFormer stem separation. Used every render. |

**Reclaimable now:** ~20GB by deleting v10+v11, +~16GB by thinning v12 intermediates. Do v10/v11
deletion only after the user signs off that v12 is the keeper (the 2026-06-28 male/female tests
passed, so this is close).

### DiffSinger checkpoints that NO LONGER EXIST (deleted 2026-06-27)
`koroki_v2`–`koroki_v9`, `koroki_yoasobi_phase1`. CLAUDE.md's experiment-history table still lists
them for the historical record, but the weights are gone. Do not go looking for them.

---

## RVC singing models — `adapters/singing/`

| File | Size | Verdict | Notes |
|---|---|---|---|
| `Korokiv5_300e_34500s_best_epoch.pth` + `Korokiv5.index` | 55M | 🟢💎 **KEEP — production + precious** | The ONLY RVC model to use. Applio training dir was deleted → these are the only surviving copy. Back up before any risky op. |
| `Korokiv4_500e_25000s.pth` + `.index` | 53M | 🔴 **DELETE candidate** | Range-starved teacher (see LEGACY 2026-06-25). Superseded by v5. |
| `Korokiv3_400e_10400s.pth` + `.index` | 53M | 🔴 **DELETE candidate** | Same. Superseded. |
| `Korokiv2_400e_8000s.pth` + `.index` | 53M | 🔴 **DELETE candidate** | The buzzy teacher that caused months of bad singing. Keep ONE copy for the record if sentimental, else delete. |

**Why v2/v3/v4 are still on disk:** harmless (~160MB total) and document the journey, but they must
**never** be selected by any pipeline. The code points at v5 only.

---

## Personality LoRA adapters — `adapters/`

| Dir | Size | Verdict | Notes |
|---|---|---|---|
| `koroki_4b` | ~140M | 🟢💎 **KEEP — production** | Current personality. Qwen3-1.7B base, rank 32 / alpha 64, 5 epochs, full-text loss. (Dir name "4b" is legacy from the pre-pivot 4B model.) |
| `koroki_4b_backup_jun20` | ~140M | 🟢 **KEEP (temporary)** | Backup taken 2026-06-28 before the clean retrain. Delete once the retrain is voice-confirmed good. |
| `koroki_personality_v1` | small | 🟡 **KEEP (reference)** | Older personality LoRA. Reference only. |

---

## TTS checkpoints — `experiments/index-tts/checkpoints/`

| File | Verdict | Notes |
|---|---|---|
| `gpt.pth`, `s2mel.pth`/`feat*.pt`, `config.yaml`, `bpe.model`, `qwen0.6bemo4-merge` | 🟢💎 **KEEP — production** | IndexTTS model. Primary speech engine. Re-downloading is slow/large. |

---

## Retention rules (so disk never fills again)

1. **One generation back, max.** When a new singing model is confirmed (e.g. v12), the previous two
   (v10, v11) become delete candidates — flag here, delete after user sign-off.
2. **Thin intermediate training checkpoints.** A finished exp keeps the **base milestone + best/final
   ckpt** only (e.g. v12 → 40000 + 74000). Delete the 10k/20k/.../72k intermediates.
3. **Never auto-delete anything marked 💎 precious** without an explicit backup first.
4. **The base (`koroki_ja_v1_160k`) and the production vocoder/separator are permanent.**
5. After any cleanup, **update this file and `docs/koroki_map.md`** — stale manifests are worse than
   none (see CLAUDE.md going stale on the deleted venvs).

**Disk history:** hit 100% once (2026-06-27), freed ~133GB by removing dead checkpoints/venvs. The
current ~30GB of DiffSinger checkpoints is the next obvious reclaim once v12 is locked in.
