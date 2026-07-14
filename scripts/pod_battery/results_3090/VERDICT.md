# 3090 Rehearsal Verdict — real RTX 3090, RunPod community pod, 2026-07-07

~48 GPU-minutes @ $0.22/hr ≈ **$0.18**. All numbers measured, not estimated
(raw JSONs beside this file; battery code in `scripts/pod_battery/`).

## The purchase case

| Question | Answer |
|---|---|
| 8B captain | **7.4GB, 114 tok/s** (2× home's 4B speed), loads in 3s |
| 14B captain | **11.2GB, 73 tok/s** — genuinely viable with room for voice+vision |
| 30B-A3B | 18.3GB, 55 tok/s — runs, but solo-tenant; experiment tier only |
| 8B Big Retrain (QLoRA, home recipe) | 11.3 s/step → **~1 hour** for 2100×5ep |
| GRPO pilot | **c512 fits: 13.3GB peak** (DeepSeek right); **c2048 OOM ~25GB** (Gemini's rollout warning right). Recipe: G=4, NF4+bf16, completion ≤512-768 |
| Photon eyes | query 0.03s warm, point 0.026s. GREEDY by default (7.7GB) — cage with max_batch_size=1 + kv_cache_pages (needs ≥~1600 pages for one 720p-frame query; 1509 tokens measured) |
| GPU whisper ears | 0.21-0.26s per 11s phrase, 2.3GB — research claim verified |
| IndexTTS2 voice | RTF 0.97 solo (no flash-attn = floor). **Stock build = 9.7GB**; home diet ≈5GB |
| **Co-stack fit** | Stock TTS: 24.0GB = byte-edge, vision can't breathe. **With home-dieted TTS: ~19GB + right-sized vision cage → ~4.5GB headroom. FITS.** |
| Contention | Under 4-organ simultaneous load: TTS RTF 0.97→2.3-3.6, llm ~68 tok/s (from 114). Real but acceptable — home rarely fires all four at once |

Speed transfer note: these ARE 3090 numbers (no derating needed). Home Windows/WDDM
takes ~0.5-1GB extra vs the Linux pod — the 4.5GB headroom covers it.

## Traps hit (for the next pod run)
- Ubuntu 24.04 PEP 668: system pip silently no-ops → `PIP_BREAK_SYSTEM_PACKAGES=1`
- Caches default to /root/.cache on the 20GB CONTAINER disk → `HF_HOME=/workspace/hf_cache` (+uv/pip cache dirs)
- ExLlamaV2 dynamic generator defaults paged → needs flash-attn → `paged=False` (same as home engine)
- TRL 1.7 dropped `GRPOConfig(max_prompt_length=)`
- IndexTTS setup.py guards py<3.12 → uv-fetched 3.11 venv
- index-tts repo ships NO audio — setup downloads openai/whisper jfk.flac
- kestrel/Photon sizes KV to free VRAM at load → load it LAST and caged
- `pkill -f 'patter[n]'` bracket trick must cover EVERY mention of the word in the same command (a `cat costack.json` in the line re-matched and killed the shell)
- RunPod env PUBLIC_KEY works; Git Bash `ssh-keygen -N '""'` sets a literal two-char passphrase — use `-N ""`
