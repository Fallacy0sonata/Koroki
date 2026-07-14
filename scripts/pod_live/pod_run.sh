#!/usr/bin/env bash
# Start her GPU organs on the pod (brain, voice, vision) and wait until healthy.
# Services bind 127.0.0.1 — reachable ONLY through the SSH tunnels. Re-runnable.
set -u
cd /workspace/koroki
export PYTHONPATH=/workspace/koroki
export KOROKI_ROOT=/workspace/koroki
export HF_HOME=/workspace/hf_cache
mkdir -p /workspace/logs

start_if_down() {  # name port cmd...
    local name="$1" port="$2"; shift 2
    if curl -s -o /dev/null --max-time 2 "http://127.0.0.1:${port}/health"; then
        echo "[$name] already up"
        return
    fi
    nohup "$@" > "/workspace/logs/${name}.log" 2>&1 &
    echo "[$name] starting (pid $!)"
}

start_if_down brain 9881 python -m uvicorn services.brain.app:app \
    --host 127.0.0.1 --port 9881 --no-access-log

start_if_down vision 9005 python -m uvicorn services.vision.main:app \
    --host 127.0.0.1 --port 9005 --no-access-log

INDEX_TTS_MODEL_DIR=/workspace/models/IndexTTS-2 \
INDEX_TTS_CFG=/workspace/models/IndexTTS-2/config.yaml \
start_if_down voice 9000 /workspace/venv_tts311/bin/python \
    experiments/index-tts/adapter.py

echo "waiting for health (brain loads EXL2+LoRA ~30s, IndexTTS ~1-2min)..."
for port in 9881 9005 9000; do
    for i in $(seq 1 60); do
        if curl -s -o /dev/null --max-time 3 "http://127.0.0.1:${port}/health"; then
            echo "  :$port healthy"
            break
        fi
        [ "$i" = "60" ] && echo "  :$port NOT UP — check /workspace/logs/"
        sleep 5
    done
done
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader
echo "pod side ready — run start_rehearsal.ps1 at home"
