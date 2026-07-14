#!/usr/bin/env bash
# Pod-side setup for the live rehearsal. Run once after sync_to_pod.ps1.
# RunPod PyTorch template (Linux). All battery traps (2026-07-07) baked in.
set -u
export PIP_BREAK_SYSTEM_PACKAGES=1
export HF_HOME=/workspace/hf_cache
export UV_CACHE_DIR=/workspace/.uv_cache
export PIP_CACHE_DIR=/workspace/.pip_cache
mkdir -p /workspace/models /workspace/logs /workspace/hf_cache

echo "== python deps (brain + vision + shared) =="
python -m pip install -q -U pip
pip install -q fastapi uvicorn pydantic pyyaml "huggingface_hub[hf_transfer]" \
    exllamav2 "transformers>=4.51" accelerate pillow einops soundfile requests \
    rapidocr-onnxruntime pyvips-binary pyvips 2>&1 | tail -2

echo "== brain model: 4B EXL2 quant (public) =="
hf download TheMelonGod/Qwen3-4B-exl2 --revision 8hb-5.0bpw \
    --local-dir /workspace/models/Qwen3-4B-exl2 2>&1 | tail -1

echo "== vision model: moondream2 (public) =="
hf download vikhyatk/moondream2 --revision 2025-06-21 \
    --local-dir /workspace/models/moondream2-2025-06-21 2>&1 | tail -1 \
    || hf download vikhyatk/moondream2 --local-dir /workspace/models/moondream2-2025-06-21 2>&1 | tail -1

echo "== voice: IndexTTS2 (py<3.12 guard -> uv 3.11 venv; battery recipe) =="
if [ ! -d /workspace/index-tts ]; then
    git clone --depth 1 https://github.com/index-tts/index-tts.git /workspace/index-tts
fi
pip install -q uv
if [ ! -f /workspace/venv_tts311/bin/python ]; then
    uv venv --python 3.11 /workspace/venv_tts311
fi
uv pip install --python /workspace/venv_tts311/bin/python -e /workspace/index-tts 2>&1 | tail -2
# adapter deps that index-tts itself doesn't pull
uv pip install --python /workspace/venv_tts311/bin/python fastapi uvicorn soundfile pyyaml 2>&1 | tail -1
hf download IndexTeam/IndexTTS-2 --local-dir /workspace/models/IndexTTS-2 2>&1 | tail -1

echo "== patch settings paths for pod =="
python /workspace/koroki/scripts/pod_live/patch_settings_pod.py

echo "== sanity =="
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
LORA_DIR=$(awk -F'"' '/^[[:space:]]+lora_dir:/{print $2; exit}' /workspace/koroki/config/settings.yaml)
[ -n "$LORA_DIR" ] && [ -d "$LORA_DIR" ] \
    && echo "LoRA present: $LORA_DIR" || echo "WARN: configured LoRA missing: $LORA_DIR"
ls /workspace/koroki/voice_samples/*.wav >/dev/null && echo "voice samples present" || echo "WARN: voice samples missing"
echo "setup done — next: bash /workspace/koroki/scripts/pod_live/pod_run.sh"
