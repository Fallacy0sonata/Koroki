#!/usr/bin/env bash
# Run every battery stage, fail soft, collect results. ~45-90 min total.
# Order: cheap solo stages first (each in its own process so VRAM readings
# are clean), training stages, then the co-residency finale.
set -u
cd "$(dirname "$0")"
mkdir -p /workspace/results
# model downloads MUST land on the big volume — the 20GB container disk
# filled up when HF cache defaulted to /root/.cache (live lesson 2026-07-07)
export HF_HOME=/workspace/hf_cache
LOG=/workspace/results/run_all.log
exec > >(tee -a "$LOG") 2>&1

stage() {
    echo ""
    echo "──────────────────────────────────────────────"
    echo "== $1  ($(date +%H:%M:%S), vram: $(nvidia-smi --query-gpu=memory.used --format=csv,noheader))"
    echo "──────────────────────────────────────────────"
    shift
    timeout 45m "$@" || echo "STAGE FAILED (soft): $*"
}

stage "whisper turbo"      python bench_whisper.py
stage "photon vision"      python bench_vision_photon.py
stage "indextts2 rtf"      /workspace/venv_tts311/bin/python bench_tts_indextts.py
stage "llm 8b"             python bench_llm.py --repo TheMelonGod/Qwen3-8B-exl2  --rev 8hb-4.5bpw --tag 8b
stage "llm 14b"            python bench_llm.py --repo TheMelonGod/Qwen3-14B-exl2 --rev 8hb-4.5bpw --tag 14b
stage "llm 30b-a3b"        python bench_llm.py --repo bullerwins/Qwen3-30B-A3B-exl2_4.0bpw --rev main --tag 30b-a3b
stage "qlora 8b"           python bench_train_qlora.py
stage "grpo c512"          python bench_grpo.py --completion 512
stage "grpo c2048"         python bench_grpo.py --completion 2048
stage "costack fit test"   python costack.py

echo ""
echo "══════════ SUMMARY ══════════"
python - <<'PY'
import json
from pathlib import Path
for f in sorted(Path("/workspace/results").glob("*.json")):
    if "costack_status" in str(f):
        continue
    d = json.loads(f.read_text())
    err = d.get("error")
    key_bits = {k: v for k, v in d.items()
                if k in ("vram_loaded_mib", "vram_peak_mib", "peak_vram_smi_mib",
                         "peak_vram_torch_mib", "tok_s_avg", "rtf_avg", "s_per_step",
                         "est_hours_2100x5ep", "transcribe_s", "query_s", "point_s",
                         "vram_peak_under_load_mib", "vram_all_loaded_mib")}
    print(f"{f.stem:20s} {'ERR: '+err[:60] if err else key_bits}")
PY
echo ""
echo "done — scp /workspace/results home, then TERMINATE the pod."
