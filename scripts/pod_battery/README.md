# Pod Battery — 24GB rehearsal for the 3090 purchase

One-shot benchmark bundle for a rented RunPod GPU (RTX 4090/3090, 24GB).
Answers the questions the 3090 purchase hangs on, with real numbers:

| Stage | Question | Home estimate to verify |
|---|---|---|
| `bench_llm.py` | 8B / 14B / 30B-A3B EXL2: VRAM, tok/s, TTFT | 8B = 6.2GB, 56 tok/s (4070Ti) |
| `bench_train_qlora.py` | 8B NF4 QLoRA (Big Retrain recipe): s/step, peak VRAM | unknown — sizes the 8B captain retrain |
| `bench_grpo.py` | GRPO G=4 peak VRAM | DS says 12–14GB, Gemini 22–23.5GB — settle it |
| `bench_whisper.py` | large-v3-turbo GPU: s/phrase, VRAM | claimed 1.6GB, ~0.2s/phrase |
| `bench_vision_photon.py` | Photon (moondream 1.3.0): query/point ms, VRAM | 0.25s query / 0.04s point / 4.2GB (local vetting) |
| `bench_tts_indextts.py` | IndexTTS2: RTF, VRAM (production-voice plan) | ~5GB, RTF unknown on 24GB-class |
| `costack.py` | THE fit test: 8B + whisper + Photon + IndexTTS resident together under load | paper math ≈ 17.5GB |

## Run order (on the pod, from /workspace)
```bash
git clone <not needed — scp this folder up> pod_battery && cd pod_battery
bash setup.sh              # installs everything, ~5-10 min on pod network
bash run_all.sh            # runs every stage, each fails soft, ~45-90 min
# results land in /workspace/results/*.json + summary.txt
```

From home, upload/fetch (pod SSH string from RunPod UI):
```powershell
scp -P <port> -r scripts\pod_battery root@<pod-ip>:/workspace/
scp -P <port> -r root@<pod-ip>:/workspace/results scripts\pod_battery\results_<gpu>
```

## Rules
- Nothing private goes up: public weights, public datasets, repo example voice wav.
- 4090 numbers: VRAM transfers 1:1 to a 3090; speed does NOT — derate ~40% (3090 ≈ 0.55–0.65× a 4090).
- Linux pod vs home Windows: WDDM steals ~0.5–1GB — demand that much headroom in fit results.
- TERMINATE the pod when done (stopped pods still bill disk).
