from __future__ import annotations

import json
import subprocess
import threading
import time
import uuid
from pathlib import Path

import httpx


def query_gpu_used_mib() -> tuple[int, int]:
    cmd = [
        "nvidia-smi",
        "--query-gpu=memory.used,memory.total",
        "--format=csv,noheader,nounits",
    ]
    raw = subprocess.check_output(cmd, text=True).strip().splitlines()[0]
    used, total = [int(x.strip()) for x in raw.split(",")[:2]]
    return used, total


def run_owner_chat(orchestrator_url: str, text: str) -> dict:
    payload = {
        "request_id": str(uuid.uuid4()),
        "message": text,
        "user_context": {
            "user_id": "owner_vram_probe",
            "relationship_score": 100,
            "is_owner": True,
            "mode": "casual",
            "platform": "discord",
        },
    }
    with httpx.Client(timeout=180.0) as client:
        r = client.post(f"{orchestrator_url}/v1/chat", json=payload)
        r.raise_for_status()
        return r.json()


def main() -> None:
    orchestrator_url = "http://127.0.0.1:9882"
    out_path = Path("data/logs/concurrent_vram_owner_trace.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Warmup once (loads owner adapter + TTS clone prompt/model path)
    run_owner_chat(orchestrator_url, "Warmup check. Say one short sentence.")

    samples: list[dict] = []
    stop_flag = {"stop": False}

    def sampler() -> None:
        while not stop_flag["stop"]:
            used, total = query_gpu_used_mib()
            samples.append({"t": time.time(), "used_mib": used, "total_mib": total})
            time.sleep(0.1)

    th = threading.Thread(target=sampler, daemon=True)

    baseline_used, total = query_gpu_used_mib()
    t0 = time.time()
    th.start()

    resp1 = run_owner_chat(orchestrator_url, "Haii~")
    resp2 = run_owner_chat(orchestrator_url, "Write a tiny grocery list in your style.")

    stop_flag["stop"] = True
    th.join(timeout=2.0)
    t1 = time.time()

    peak_used = max((s["used_mib"] for s in samples), default=baseline_used)

    result = {
        "orchestrator_url": orchestrator_url,
        "baseline_used_mib": baseline_used,
        "peak_used_mib": peak_used,
        "delta_peak_mib": peak_used - baseline_used,
        "gpu_total_mib": total,
        "duration_s": round(t1 - t0, 3),
        "responses": [
            {"text_preview": str(resp1.get("text", ""))[:140]},
            {"text_preview": str(resp2.get("text", ""))[:140]},
        ],
        "sample_count": len(samples),
    }

    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
