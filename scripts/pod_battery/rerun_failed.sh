#!/usr/bin/env bash
# Rerun the stages that failed in the first full pass (2026-07-07 live session).
# Idempotent: stages overwrite their own result JSONs.
set -u
cd "$(dirname "$0")"
export HF_HOME=/workspace/hf_cache
export PIP_BREAK_SYSTEM_PACKAGES=1

stage() {
    echo ""
    echo "──────────────────────────────────────────────"
    echo "== $1  ($(date +%H:%M:%S), vram: $(nvidia-smi --query-gpu=memory.used --format=csv,noheader))"
    echo "──────────────────────────────────────────────"
    shift
    timeout 45m "$@" || echo "STAGE FAILED (soft): $*"
}

stage "whisper turbo"      python bench_whisper.py
stage "indextts2 rtf"      /workspace/venv_tts311/bin/python bench_tts_indextts.py
stage "llm 30b-a3b"        python bench_llm.py --repo bullerwins/Qwen3-30B-A3B-exl2_4.0bpw --rev main --tag 30b-a3b
stage "grpo c512"          python bench_grpo.py --completion 512
stage "grpo c2048"         python bench_grpo.py --completion 2048
stage "costack fit test"   python costack.py

echo "══════════ RERUN DONE ══════════"
