#!/usr/bin/env bash
# Pod battery setup — RunPod PyTorch template (Linux, CUDA preinstalled).
# Everything fails soft: a broken stage skips, the rest still run.
set -u
cd "$(dirname "$0")"
mkdir -p /workspace/results /workspace/models

export HF_HUB_ENABLE_HF_TRANSFER=1
export DEBIAN_FRONTEND=noninteractive
# ubuntu 24.04 marks system python externally-managed (PEP 668) — every pip
# install silently no-ops without this. Throwaway container: break away.
export PIP_BREAK_SYSTEM_PACKAGES=1
# all caches on the 120GB volume, never the 20GB container disk
export HF_HOME=/workspace/hf_cache
export UV_CACHE_DIR=/workspace/.uv_cache
export PIP_CACHE_DIR=/workspace/.pip_cache

echo "== base =="
python -m pip install -q -U pip
pip install -q "huggingface_hub[hf_transfer]" safetensors sentencepiece protobuf soundfile

echo "== inference (exllamav2) =="
pip install -q exllamav2 || echo "WARN: exllamav2 pip failed — bench_llm will skip"

echo "== training (qlora/grpo) =="
pip install -q "transformers>=4.51" datasets accelerate peft trl bitsandbytes

echo "== ears (whisper) =="
pip install -q faster-whisper

echo "== eyes (photon) =="
pip install -q moondream==1.3.0 pillow || echo "WARN: moondream pip failed — vision stage will skip"

echo "== voice (indextts — setup.py guards py<3.12, so uv fetches a 3.11 venv) =="
if [ ! -d /workspace/index-tts ]; then
    git clone --depth 1 https://github.com/index-tts/index-tts.git /workspace/index-tts \
        || echo "WARN: index-tts clone failed"
fi
if [ -d /workspace/index-tts ]; then
    pip install -q uv
    uv venv --python 3.11 /workspace/venv_tts311 \
        && uv pip install --python /workspace/venv_tts311/bin/python -e /workspace/index-tts \
        || echo "WARN: indextts deps failed — tts stage will skip"
    hf download IndexTeam/IndexTTS-2 --local-dir /workspace/models/IndexTTS-2 \
        || huggingface-cli download IndexTeam/IndexTTS-2 --local-dir /workspace/models/IndexTTS-2 \
        || echo "WARN: IndexTTS-2 weights download failed"
fi

echo "== speech sample (index-tts ships no audio; whisper + tts stages need one) =="
if [ ! -f /workspace/sample_speech.wav ]; then
    curl -sL -o /workspace/jfk.flac https://github.com/openai/whisper/raw/main/tests/jfk.flac
    python - <<'PY'
import numpy as np, soundfile as sf
d, sr = sf.read("/workspace/jfk.flac", dtype="float32")
if d.ndim > 1: d = d.mean(axis=1)
if sr != 16000:
    idx = np.linspace(0, len(d) - 1, int(len(d) * 16000 / sr))
    d = np.interp(idx, np.arange(len(d)), d).astype("float32")
sf.write("/workspace/sample_speech.wav", d, 16000)
print("speech sample ready:", len(d) / 16000, "s")
PY
fi

echo "== sanity =="
nvidia-smi
python - <<'PY'
import torch
print("torch", torch.__version__, "| cuda", torch.cuda.is_available(),
      "|", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "-")
PY
echo "setup done."
